#!/usr/bin/env python3
"""
Feedmaster Bluesky Achievement Bot

Polls Feedmaster API for new achievements and posts them to Bluesky.
"""

import asyncio
import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import httpx
from atproto import Client, models
from atproto.exceptions import AtProtocolError
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import re
import hashlib
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FeedmasterBlueskyBot:
    def __init__(self):
        # Load configuration from environment
        self.feedmaster_api_url = os.getenv('FEEDMASTER_API_URL', 'https://feedmaster.fema.monster')
        self.feed_ids = os.getenv('FEED_IDS', '').split(',')
        self.bluesky_username = os.getenv('BLUESKY_USERNAME')
        self.bluesky_did = os.getenv('BLUESKY_DID')  # Optional DID fallback
        self.bluesky_app_password = os.getenv('BLUESKY_APP_PASSWORD')
        self.min_rarity_tier = os.getenv('MIN_RARITY_TIER', 'Bronze')
        self.poll_interval_minutes = int(os.getenv('POLL_INTERVAL_MINUTES', '10'))
        self.max_posts_per_hour = int(os.getenv('MAX_POSTS_PER_HOUR', '30'))
        
        # Validation - enforce limits
        if self.poll_interval_minutes < 10:
            raise ValueError(f"POLL_INTERVAL_MINUTES must be at least 10 minutes, got {self.poll_interval_minutes}")
        if self.max_posts_per_hour > 60:
            raise ValueError(f"MAX_POSTS_PER_HOUR cannot exceed 60, got {self.max_posts_per_hour}")
        
        # Calculate posts per interval dynamically
        polls_per_hour = 60 / self.poll_interval_minutes
        self.max_posts_per_interval = max(1, int(self.max_posts_per_hour / polls_per_hour))
        self.message_template = os.getenv(
            'MESSAGE_TEMPLATE',
            '🎉 Congratulations {display_name} on earning "{achievement}"! Only {percentage}% of users have achieved this {rarity} rarity!'
        )
        
        # Validate configuration
        if (not self.bluesky_username and not self.bluesky_did) or not self.bluesky_app_password:
            raise ValueError("BLUESKY_USERNAME (or BLUESKY_DID) and BLUESKY_APP_PASSWORD are required")
        
        if not self.feed_ids or self.feed_ids == ['']:
            raise ValueError("FEED_IDS is required")
        
        # Initialize Bluesky client
        self.bluesky_client = None
        
        # Rate limiting
        self.posts_this_hour = 0
        self.hour_reset_time = datetime.now() + timedelta(hours=1)
        
        # Track cursor for processed achievements
        self.cursor_file = '/tmp/achievement_cursor.txt'
        self.last_processed_id = self._load_cursor()
        
        # Rarity tier ordering for filtering
        self.rarity_order = {
            'Bronze': 0,
            'Silver': 1, 
            'Gold': 2,
            'Platinum': 3,
            'Diamond': 4,
            'Legendary': 5,
            'Mythic': 6
        }
        
        logger.info(f"Bot initialized for feeds: {self.feed_ids}")
        logger.info(f"Minimum rarity: {self.min_rarity_tier}")
        logger.info(f"Poll interval: {self.poll_interval_minutes} minutes")
        logger.info(f"Max posts per hour: {self.max_posts_per_hour}")
        logger.info(f"Max posts per interval: {self.max_posts_per_interval}")
        logger.info(f"Starting from achievement ID: {self.last_processed_id}")
        
        # Initialize image generator
        self.cache_dir = "/tmp/achievement_cards"
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def _load_cursor(self) -> int:
        """Load last processed achievement ID from file"""
        try:
            if os.path.exists(self.cursor_file):
                with open(self.cursor_file, 'r') as f:
                    return int(f.read().strip())
        except Exception as e:
            logger.warning(f"Failed to load cursor: {e}")
        return 0  # Start from beginning if no cursor
    
    def _save_cursor(self, achievement_id: int):
        """Save last processed achievement ID to file"""
        try:
            with open(self.cursor_file, 'w') as f:
                f.write(str(achievement_id))
        except Exception as e:
            logger.warning(f"Failed to save cursor: {e}")
    
    async def authenticate_bluesky(self):
        """Authenticate with Bluesky"""
        try:
            self.bluesky_client = Client()
            # Try username first, then DID as fallback
            login_identifier = self.bluesky_username or self.bluesky_did
            self.bluesky_client.login(login_identifier, self.bluesky_app_password)
            logger.info(f"Successfully authenticated with Bluesky as {login_identifier}")
        except Exception as e:
            logger.error(f"Failed to authenticate with Bluesky: {e}")
            raise
    
    async def get_recent_achievements(self) -> List[Dict]:
        """Fetch recent achievements from Feedmaster API"""
        try:
            feed_ids_str = ','.join(self.feed_ids)
            url = f"{self.feedmaster_api_url}/api/v1/achievements/recent"
            params = {
                'feed_ids': feed_ids_str,
                'since_id': self.last_processed_id,
                'limit': 50
            }
            
            logger.info(f"Fetching achievements from: {url}")
            logger.info(f"Params: {params}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)
                logger.info(f"API Response status: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                
                achievements = data.get('achievements', [])
                logger.info(f"Found {len(achievements)} recent achievements")
                return achievements
                
        except Exception as e:
            logger.error(f"Failed to fetch achievements: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return []
    
    def should_post_achievement(self, achievement: Dict) -> bool:
        """Check if achievement meets posting criteria"""
        rarity_tier = achievement.get('rarity_tier')
        
        # Skip achievements with null/missing rarity (not yet calculated)
        if not rarity_tier:
            logger.warning(f"Skipping achievement {achievement.get('achievement_name')} - rarity not calculated yet")
            return False
            
        min_rarity_level = self.rarity_order.get(self.min_rarity_tier, 0)
        achievement_rarity_level = self.rarity_order.get(rarity_tier, 0)
        
        return achievement_rarity_level >= min_rarity_level
    
    def format_message(self, achievement: Dict) -> tuple[str, Optional[str]]:
        """Format the Bluesky post message and return message + share_url"""
        username = achievement.get('user_handle', 'unknown')
        display_name = achievement.get('user_display_name')
        if not display_name or display_name.strip() == '':
            display_name = username
        achievement_name = achievement.get('achievement_name', 'Unknown Achievement')
        rarity_tier = achievement.get('rarity_tier', 'Bronze')
        rarity_percentage = achievement.get('rarity_percentage', 0)
        
        # Format the message
        message = self.message_template.format(
            username=username,
            display_name=display_name,
            achievement=achievement_name,
            rarity=rarity_tier,
            percentage=f"{rarity_percentage:.2f}"
        )
        
        share_url = achievement.get('share_url')
        return message, share_url
    
    async def fetch_url_metadata(self, url: str) -> Optional[Dict]:
        """Fetch metadata for URL to create link card"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(
                    timeout=30.0, 
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=10)
                ) as client:
                    response = await client.get(url, headers={
                        'User-Agent': 'Mozilla/5.0 (compatible; FeedmasterBot/1.0)',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.5',
                        'Accept-Encoding': 'gzip, deflate',
                        'Connection': 'keep-alive'
                    })
                    response.raise_for_status()
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Extract metadata with better fallbacks
                    title = None
                    description = None
                    image_url = None
                    
                    # Try Open Graph tags first
                    og_title = soup.find('meta', property='og:title')
                    if og_title and og_title.get('content'):
                        title = og_title.get('content').strip()
                    
                    og_description = soup.find('meta', property='og:description')
                    if og_description and og_description.get('content'):
                        description = og_description.get('content').strip()
                    
                    og_image = soup.find('meta', property='og:image')
                    if og_image and og_image.get('content'):
                        image_url = og_image.get('content').strip()
                    
                    # Fallback to standard meta tags
                    if not title:
                        title_tag = soup.find('title')
                        if title_tag and title_tag.get_text():
                            title = title_tag.get_text().strip()
                    
                    if not description:
                        desc_tag = soup.find('meta', attrs={'name': 'description'})
                        if desc_tag and desc_tag.get('content'):
                            description = desc_tag.get('content').strip()
                    
                    # Ensure we have required fields
                    if not title:
                        title = "Achievement Unlocked"
                    if not description:
                        description = "View this achievement on Feedmaster"
                    
                    # Download and upload image to Bluesky if available
                    image_blob = None
                    if image_url:
                        try:
                            # Make image URL absolute if relative
                            if image_url.startswith('//'):
                                image_url = 'https:' + image_url
                            elif image_url.startswith('/'):
                                from urllib.parse import urljoin
                                image_url = urljoin(url, image_url)
                            
                            img_response = await client.get(image_url, timeout=15.0)
                            img_response.raise_for_status()
                            
                            # Check content type
                            content_type = img_response.headers.get('content-type', '')
                            if content_type.startswith('image/'):
                                # Upload image to Bluesky
                                upload_result = self.bluesky_client.upload_blob(img_response.content)
                                image_blob = upload_result.blob if hasattr(upload_result, 'blob') else upload_result
                                logger.info(f"Uploaded image blob for {url}")
                            else:
                                logger.warning(f"Invalid image content type: {content_type}")
                        except Exception as e:
                            logger.warning(f"Failed to upload image for {url}: {e}")
                    
                    # Properly encode the URL before returning
                    from urllib.parse import quote, urlparse, urlunparse
                    parsed = urlparse(url)
                    encoded_path = quote(parsed.path, safe='/')
                    encoded_url = urlunparse((parsed.scheme, parsed.netloc, encoded_path, parsed.params, parsed.query, parsed.fragment))
                    
                    return {
                        'title': title[:300],
                        'description': description[:300],
                        'image_blob': image_blob,
                        'url': encoded_url
                    }
                    
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed to fetch metadata for {url}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    logger.error(f"All {max_retries} attempts failed for {url}")
        
        return None
    
    def _get_font(self, size: int):
        """Get font with multiple fallbacks - FIXED VERSION"""
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ]
        
        for font_path in font_paths:
            try:
                return ImageFont.truetype(font_path, size)
            except:
                continue
        
        # CRITICAL FIX: If all fail, use default but make it MUCH bigger
        logger.warning(f"All fonts failed, using default with size {size * 3}")
        try:
            return ImageFont.load_default(size * 3)
        except:
            return ImageFont.load_default()
    
    async def _download_avatar(self, avatar_url: str) -> Image.Image:
        """Download and process user avatar"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(avatar_url, timeout=10)
                response.raise_for_status()
                avatar = Image.open(BytesIO(response.content)).convert('RGBA')
                return avatar.resize((160, 160), Image.Resampling.LANCZOS)
        except Exception as e:
            logger.warning(f"Failed to download avatar {avatar_url}: {e}")
            # Return default avatar
            avatar = Image.new('RGBA', (160, 160), (64, 68, 75, 255))
            draw = ImageDraw.Draw(avatar)
            draw.ellipse([0, 0, 160, 160], fill=(100, 100, 100, 255))
            return avatar
    
    async def generate_achievement_card(self, achievement: Dict) -> Optional[str]:
        """Generate achievement card image and return file path"""
        try:
            user_name = achievement.get('user_display_name') or achievement.get('user_handle', 'Unknown')
            achievement_name = achievement.get('achievement_name', 'Unknown Achievement')
            rarity_tier = achievement.get('rarity_tier', 'Bronze')
            user_avatar_url = achievement.get('user_avatar_url', '')
            
            # Generate cache key
            content = f"{user_avatar_url}:{achievement_name}:{user_name}:v16_PERFECT"
            cache_key = hashlib.md5(content.encode()).hexdigest()
            cache_path = os.path.join(self.cache_dir, f"{cache_key}.png")
            
            # Return cached version if exists
            if os.path.exists(cache_path):
                return cache_path
            
            # Create new card
            width, height = 1200, 630
            
            # Create gradient background
            img = Image.new('RGB', (width, height))
            draw = ImageDraw.Draw(img)
            
            # Create gradient from dark blue to purple
            for y in range(height):
                ratio = y / height
                r = int(43 + (88 - 43) * ratio)  # 43 -> 88
                g = int(45 + (101 - 45) * ratio)  # 45 -> 101
                b = int(49 + (242 - 49) * ratio)  # 49 -> 242
                draw.line([(0, y), (width, y)], fill=(r, g, b))
            
            # Download and add user avatar if available
            if user_avatar_url:
                avatar = await self._download_avatar(user_avatar_url)
                
                # Make avatar circular
                mask = Image.new('L', (160, 160), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.ellipse([0, 0, 160, 160], fill=255)
                
                # Create circular avatar
                circular_avatar = Image.new('RGBA', (160, 160), (0, 0, 0, 0))
                circular_avatar.paste(avatar, (0, 0))
                circular_avatar.putalpha(mask)
                
                # Add white border around avatar
                border_size = 6
                border_avatar = Image.new('RGBA', (160 + border_size * 2, 160 + border_size * 2), (255, 255, 255, 255))
                border_mask = Image.new('L', (160 + border_size * 2, 160 + border_size * 2), 0)
                border_draw = ImageDraw.Draw(border_mask)
                border_draw.ellipse([0, 0, 160 + border_size * 2, 160 + border_size * 2], fill=255)
                border_avatar.putalpha(border_mask)
                
                # Paste bordered avatar
                img.paste(border_avatar, (width // 2 - 86, height // 2 - 86), border_avatar)
                img.paste(circular_avatar, (width // 2 - 80, height // 2 - 80), circular_avatar)
            
            # Add "feedmaster" text at top
            title_font = self._get_font(36)
            title_text = "feedmaster"
            title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
            title_width = title_bbox[2] - title_bbox[0]
            draw.text((width // 2 - title_width // 2, 50), title_text, fill=(255, 255, 255), font=title_font)
            
            # Add achievement name
            achievement_font = self._get_font(14)
            achievement_bbox = draw.textbbox((0, 0), achievement_name, font=achievement_font)
            achievement_width = achievement_bbox[2] - achievement_bbox[0]
            draw.text((width // 2 - achievement_width // 2, height - 220), achievement_name, fill=(255, 255, 255), font=achievement_font)
            
            # Add user name
            user_font = self._get_font(12)
            user_bbox = draw.textbbox((0, 0), user_name, font=user_font)
            user_width = user_bbox[2] - user_bbox[0]
            draw.text((width // 2 - user_width // 2, height - 155), user_name, fill=(220, 221, 222), font=user_font)
            
            # Add rarity badge
            rarity_colors = {
                'Mythic': (255, 0, 255),
                'Legendary': (148, 0, 211),
                'Diamond': (185, 242, 255),
                'Platinum': (229, 228, 226),
                'Gold': (255, 215, 0),
                'Silver': (192, 192, 192),
                'Bronze': (205, 127, 50)
            }
            rarity_color = rarity_colors.get(rarity_tier, (205, 127, 50))
            
            rarity_font = self._get_font(10)
            rarity_text = f"{rarity_tier} Achievement"
            rarity_bbox = draw.textbbox((0, 0), rarity_text, font=rarity_font)
            rarity_width = rarity_bbox[2] - rarity_bbox[0]
            rarity_height = rarity_bbox[3] - rarity_bbox[1]
            
            # Draw rarity badge background
            badge_padding = 6
            badge_x = width // 2 - rarity_width // 2 - badge_padding
            badge_y = height - 50 - rarity_height - badge_padding
            draw.rounded_rectangle([badge_x, badge_y, badge_x + rarity_width + (badge_padding * 2), badge_y + rarity_height + (badge_padding * 2)], 
                                 radius=8, fill=rarity_color)
            draw.text((width // 2 - rarity_width // 2, height - 50 - rarity_height), rarity_text, fill=(0, 0, 0), font=rarity_font)
            
            # Save to cache
            img.save(cache_path, 'PNG', optimize=True)
            logger.info(f"Generated achievement card: {cache_path}")
            
            return cache_path
            
        except Exception as e:
            logger.error(f"Failed to generate achievement card: {e}")
            return None
    
    async def post_to_bluesky(self, message: str, achievement: Dict, share_url: Optional[str] = None) -> bool:
        """Post message to Bluesky with optional link card"""
        try:
            # Check rate limiting
            now = datetime.now()
            if now >= self.hour_reset_time:
                self.posts_this_hour = 0
                self.hour_reset_time = now + timedelta(hours=1)
            
            if self.posts_this_hour >= self.max_posts_per_hour:
                logger.warning(f"Rate limit reached ({self.max_posts_per_hour} posts/hour). Skipping post.")
                return False
            
            # Create post with achievement card embed
            embed = None
            
            # Generate achievement card image
            card_path = await self.generate_achievement_card(achievement)
            if card_path and os.path.exists(card_path):
                try:
                    # Upload achievement card image to Bluesky
                    with open(card_path, 'rb') as f:
                        card_data = f.read()
                    
                    upload_result = self.bluesky_client.upload_blob(card_data)
                    image_blob = upload_result.blob if hasattr(upload_result, 'blob') else upload_result
                    
                    # Create external embed with achievement card
                    if share_url:
                        external = models.AppBskyEmbedExternal.External(
                            uri=share_url,
                            title=f"{achievement.get('achievement_name', 'Achievement Unlocked')}",
                            description=f"{achievement.get('user_display_name') or achievement.get('user_handle', 'User')} earned this {achievement.get('rarity_tier', 'Bronze')} achievement!",
                            thumb=image_blob
                        )
                        embed = models.AppBskyEmbedExternal.Main(external=external)
                        logger.info(f"Created achievement card link card")
                    else:
                        # If no share URL, just post the image
                        embed = models.AppBskyEmbedImages.Main(
                            images=[models.AppBskyEmbedImages.Image(
                                alt="Achievement card",
                                image=image_blob
                            )]
                        )
                        logger.info(f"Created achievement card image embed")
                        
                except Exception as e:
                    logger.error(f"Failed to upload achievement card: {e}")
                    embed = None
            else:
                logger.warning(f"Failed to generate achievement card, falling back to text-only post")
            
            # Post to Bluesky with or without embed
            if embed:
                self.bluesky_client.send_post(text=message, embed=embed)
                logger.info(f"Posted with link card")
            else:
                self.bluesky_client.send_post(text=message)
                logger.info(f"Posted text-only (no link card available)")
            
            self.posts_this_hour += 1
            
            logger.info(f"Successfully posted to Bluesky ({self.posts_this_hour}/{self.max_posts_per_hour} this hour)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to post to Bluesky: {e}")
            return False
    
    async def process_achievements(self):
        """Process and post recent achievements"""
        achievements = await self.get_recent_achievements()
        
        # Filter achievements that meet posting criteria
        eligible_achievements = []
        for achievement in achievements:
            if self.should_post_achievement(achievement):
                eligible_achievements.append(achievement)
        
        # Sort by rarity (rarest first) - lower percentage = rarer
        eligible_achievements.sort(key=lambda x: x.get('rarity_percentage', 100))
        
        # Limit to max posts per interval
        achievements_to_post = eligible_achievements[:self.max_posts_per_interval]
        
        posted_count = 0
        for achievement in achievements_to_post:
            message, share_url = self.format_message(achievement)
            
            logger.info(f"Posting achievement: {achievement['user_handle']} - {achievement['achievement_name']} ({achievement.get('rarity_percentage', 0):.2f}% rarity)")
            
            if await self.post_to_bluesky(message, achievement, share_url):
                posted_count += 1
                # Small delay between posts
                await asyncio.sleep(2)
            else:
                # If we hit rate limit, stop processing
                break
        
        # Update cursor to latest achievement ID (even if not posted)
        if achievements:
            latest_id = max(achievement.get('id', 0) for achievement in achievements)
            if latest_id > self.last_processed_id:
                self.last_processed_id = latest_id
                self._save_cursor(latest_id)
        
        logger.info(f"Posted {posted_count}/{len(achievements_to_post)} eligible achievements (limited by max_posts_per_interval={self.max_posts_per_interval})")
        logger.info(f"Processed up to achievement ID: {self.last_processed_id}")
    
    async def run(self):
        """Main bot loop"""
        logger.info("Starting Feedmaster Bluesky Bot...")
        
        # Authenticate with Bluesky
        await self.authenticate_bluesky()
        
        while True:
            try:
                await self.process_achievements()
                
                # Wait for next poll
                logger.info(f"Sleeping for {self.poll_interval_minutes} minutes...")
                await asyncio.sleep(self.poll_interval_minutes * 60)
                
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                # Wait a bit before retrying
                await asyncio.sleep(60)

async def main():
    bot = FeedmasterBlueskyBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
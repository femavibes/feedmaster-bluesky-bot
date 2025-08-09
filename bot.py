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
    
    async def post_to_bluesky(self, message: str, share_url: Optional[str] = None) -> bool:
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
            
            # Create post with optional embed
            embed = None
            if share_url:
                logger.info(f"Attempting to fetch metadata for: {share_url}")
                metadata = await self.fetch_url_metadata(share_url)
                if metadata:
                    # Create external embed for link card with properly encoded URL
                    external = models.AppBskyEmbedExternal.External(
                        uri=metadata['url'],  # This is already encoded in fetch_url_metadata
                        title=metadata['title'],
                        description=metadata['description']
                    )
                    
                    # Add image if available
                    if metadata.get('image_blob'):
                        external.thumb = metadata['image_blob']
                    
                    embed = models.AppBskyEmbedExternal.Main(external=external)
                    logger.info(f"Created link card for: {metadata['title']}")
                else:
                    logger.warning(f"Failed to fetch metadata for: {share_url}")
            
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
            
            if await self.post_to_bluesky(message, share_url):
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
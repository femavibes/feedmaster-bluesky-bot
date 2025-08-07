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
from atproto import Client
from dotenv import load_dotenv

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
        self.poll_interval_minutes = int(os.getenv('POLL_INTERVAL_MINUTES', '5'))
        self.max_posts_per_hour = int(os.getenv('MAX_POSTS_PER_HOUR', '10'))
        self.message_template = os.getenv(
            'MESSAGE_TEMPLATE',
            '🎉 Congratulations {display_name} on earning "{achievement}"! Only {percentage}% of users have achieved this {rarity} rarity! Track your achievements at feedmaster.fema.monster'
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
        
        # Track last check time
        self.last_check = datetime.now() - timedelta(hours=1)  # Start 1 hour ago
        
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
                'since': self.last_check.isoformat(),
                'limit': 50
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                achievements = data.get('achievements', [])
                logger.info(f"Found {len(achievements)} recent achievements")
                return achievements
                
        except Exception as e:
            logger.error(f"Failed to fetch achievements: {e}")
            return []
    
    def should_post_achievement(self, achievement: Dict) -> bool:
        """Check if achievement meets posting criteria"""
        rarity_tier = achievement.get('rarity_tier', 'Bronze')
        min_rarity_level = self.rarity_order.get(self.min_rarity_tier, 0)
        achievement_rarity_level = self.rarity_order.get(rarity_tier, 0)
        
        return achievement_rarity_level >= min_rarity_level
    
    def format_message(self, achievement: Dict) -> str:
        """Format the Bluesky post message"""
        username = achievement.get('user_handle', 'unknown')
        display_name = achievement.get('user_display_name') or username
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
        
        # Add share URL
        share_url = achievement.get('share_url', '')
        if share_url:
            message += f"\n\n{share_url}"
        
        return message
    
    async def post_to_bluesky(self, message: str) -> bool:
        """Post message to Bluesky"""
        try:
            # Check rate limiting
            now = datetime.now()
            if now >= self.hour_reset_time:
                self.posts_this_hour = 0
                self.hour_reset_time = now + timedelta(hours=1)
            
            if self.posts_this_hour >= self.max_posts_per_hour:
                logger.warning(f"Rate limit reached ({self.max_posts_per_hour} posts/hour). Skipping post.")
                return False
            
            # Post to Bluesky
            self.bluesky_client.send_post(text=message)
            self.posts_this_hour += 1
            
            logger.info(f"Successfully posted to Bluesky ({self.posts_this_hour}/{self.max_posts_per_hour} this hour)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to post to Bluesky: {e}")
            return False
    
    async def process_achievements(self):
        """Process and post recent achievements"""
        achievements = await self.get_recent_achievements()
        
        posted_count = 0
        for achievement in achievements:
            if self.should_post_achievement(achievement):
                message = self.format_message(achievement)
                
                logger.info(f"Posting achievement: {achievement['user_handle']} - {achievement['achievement_name']}")
                
                if await self.post_to_bluesky(message):
                    posted_count += 1
                    # Small delay between posts
                    await asyncio.sleep(2)
                else:
                    # If we hit rate limit, stop processing
                    break
        
        logger.info(f"Posted {posted_count} achievements")
        
        # Update last check time
        if achievements:
            # Use the most recent achievement time, or current time if no achievements
            latest_time = max(
                datetime.fromisoformat(a['earned_at'].replace('Z', '+00:00')) 
                for a in achievements
            )
            self.last_check = max(self.last_check, latest_time)
        else:
            self.last_check = datetime.now()
    
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
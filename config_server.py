#!/usr/bin/env python3
"""
Feedmaster Bluesky Bot Configuration Server

Simple web interface for configuring the bot without needing a domain.
Access via http://localhost:8080
"""

import os
import subprocess
from flask import Flask, render_template_string, request, redirect, flash, jsonify
from dotenv import load_dotenv, set_key
import logging

app = Flask(__name__)
app.secret_key = 'feedmaster-bot-config-secret'

# Basic auth for security
from functools import wraps
from flask import request, Response

def check_auth(username, password):
    """Check if username/password is valid"""
    return username == os.getenv('CONFIG_USERNAME', 'admin') and password == os.getenv('CONFIG_PASSWORD', 'changeme')

def authenticate():
    """Send 401 response for authentication"""
    return Response('Authentication required', 401, {'WWW-Authenticate': 'Basic realm="Bot Config"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ENV_FILE = '.env'

def load_config():
    """Load current configuration from .env file"""
    load_dotenv(ENV_FILE)
    return {
        'FEEDMASTER_API_URL': os.getenv('FEEDMASTER_API_URL', 'https://feedmaster.fema.monster'),
        'FEED_IDS': os.getenv('FEED_IDS', ''),
        'BLUESKY_USERNAME': os.getenv('BLUESKY_USERNAME', ''),
        'BLUESKY_DID': os.getenv('BLUESKY_DID', ''),
        'BLUESKY_APP_PASSWORD': os.getenv('BLUESKY_APP_PASSWORD', ''),
        'DISCORD_WEBHOOK_URL': os.getenv('DISCORD_WEBHOOK_URL', ''),
        'MIN_RARITY_TIER': os.getenv('MIN_RARITY_TIER', 'Bronze'),
        'POLL_INTERVAL_MINUTES': os.getenv('POLL_INTERVAL_MINUTES', '10'),
        'MAX_POSTS_PER_HOUR': os.getenv('MAX_POSTS_PER_HOUR', '30'),
        'MESSAGE_TEMPLATE': os.getenv('MESSAGE_TEMPLATE', '🎉 Congratulations {display_name} on earning "{achievement}"! Only {percentage}% of users have achieved this {rarity} rarity!'),
        'CONFIG_USERNAME': os.getenv('CONFIG_USERNAME', 'admin'),
        'CONFIG_PASSWORD': os.getenv('CONFIG_PASSWORD', 'changeme')
    }

def save_config(config):
    """Save configuration to .env file"""
    for key, value in config.items():
        set_key(ENV_FILE, key, value)



CONFIG_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Feedmaster Bluesky Bot Configuration</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input, select, textarea { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
        textarea { height: 60px; }
        button { background: #007cba; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; margin-right: 10px; }
        button:hover { background: #005a87; }

        .help { font-size: 0.9em; color: #666; margin-top: 5px; }
        .section { border: 1px solid #ddd; padding: 20px; margin-bottom: 20px; border-radius: 4px; }
        h2 { color: #333; border-bottom: 2px solid #007cba; padding-bottom: 10px; }
        .alert { padding: 10px; margin: 10px 0; border-radius: 4px; }
        .alert-success { background: #d4edda; color: #155724; }
        .alert-error { background: #f8d7da; color: #721c24; }
    </style>
</head>
<body>
    <h1>🤖 Feedmaster Bluesky Bot Configuration</h1>

    {% with messages = get_flashed_messages() %}
        {% if messages %}
            {% for message in messages %}
                <div class="alert alert-success">{{ message }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <form method="POST">
        <div class="section">
            <h2>🔗 Feedmaster Connection</h2>
            <div class="form-group">
                <label for="FEEDMASTER_API_URL">Feedmaster API URL:</label>
                <input type="url" name="FEEDMASTER_API_URL" value="{{ config.FEEDMASTER_API_URL }}" required>
                <div class="help">The URL of your Feedmaster instance (e.g., https://feedmaster.fema.monster)</div>
            </div>
            <div class="form-group">
                <label for="FEED_IDS">Feed IDs:</label>
                <input type="text" name="FEED_IDS" value="{{ config.FEED_IDS }}" placeholder="1234,5555" required>
                <div class="help">Comma-separated list of feed IDs to monitor (e.g., 1234,5555)</div>
            </div>
        </div>

        <div class="section">
            <h2>📱 Platform Configuration</h2>
            <div class="alert" style="background: #e7f3ff; color: #0c5460; border: 1px solid #b8daff; margin-bottom: 15px;">
                <strong>Note:</strong> Configure at least one platform (Bluesky or Discord). You can use both simultaneously.
            </div>
            
            <h3>🦋 Bluesky Account (Optional)</h3>
            <div class="form-group">
                <label for="BLUESKY_USERNAME">Bluesky Username:</label>
                <input type="text" name="BLUESKY_USERNAME" value="{{ config.BLUESKY_USERNAME }}" placeholder="your-bot.bsky.social">
                <div class="help">Your bot's Bluesky handle (leave empty if using DID)</div>
            </div>
            <div class="form-group">
                <label for="BLUESKY_DID">Bluesky DID (optional):</label>
                <input type="text" name="BLUESKY_DID" value="{{ config.BLUESKY_DID }}" placeholder="did:plc:...">
                <div class="help">Alternative to username - use DID if you have it</div>
            </div>
            <div class="form-group">
                <label for="BLUESKY_APP_PASSWORD">App Password:</label>
                <input type="password" name="BLUESKY_APP_PASSWORD" value="{{ config.BLUESKY_APP_PASSWORD }}">
                <div class="help">Generate this in Bluesky Settings > App Passwords</div>
            </div>
            
            <h3>💬 Discord Webhook (Optional)</h3>
            <div class="form-group">
                <label for="DISCORD_WEBHOOK_URL">Discord Webhook URL:</label>
                <input type="url" name="DISCORD_WEBHOOK_URL" value="{{ config.DISCORD_WEBHOOK_URL }}" placeholder="https://discord.com/api/webhooks/...">
                <div class="help">Get this from Discord Server Settings > Integrations > Webhooks</div>
            </div>
        </div>

        <div class="section">
            <h2>⚙️ Bot Settings</h2>
            <div class="form-group">
                <label for="MIN_RARITY_TIER">Minimum Rarity Tier:</label>
                <select name="MIN_RARITY_TIER">
                    <option value="Bronze" {{ 'selected' if config.MIN_RARITY_TIER == 'Bronze' else '' }}>Bronze (most achievements)</option>
                    <option value="Silver" {{ 'selected' if config.MIN_RARITY_TIER == 'Silver' else '' }}>Silver</option>
                    <option value="Gold" {{ 'selected' if config.MIN_RARITY_TIER == 'Gold' else '' }}>Gold</option>
                    <option value="Platinum" {{ 'selected' if config.MIN_RARITY_TIER == 'Platinum' else '' }}>Platinum</option>
                    <option value="Diamond" {{ 'selected' if config.MIN_RARITY_TIER == 'Diamond' else '' }}>Diamond</option>
                    <option value="Legendary" {{ 'selected' if config.MIN_RARITY_TIER == 'Legendary' else '' }}>Legendary</option>
                    <option value="Mythic" {{ 'selected' if config.MIN_RARITY_TIER == 'Mythic' else '' }}>Mythic (rarest)</option>
                </select>
                <div class="help">Only post achievements at or above this rarity level</div>
            </div>
            <div class="form-group">
                <label for="POLL_INTERVAL_MINUTES">Poll Interval (minutes):</label>
                <input type="number" name="POLL_INTERVAL_MINUTES" value="{{ config.POLL_INTERVAL_MINUTES }}" min="10" max="60" oninput="updateBotBehavior()">
                <div class="help">How often to check for new achievements. <strong>MINIMUM 10 minutes</strong> - bot will not start if set lower!</div>
            </div>
            <div class="form-group">
                <label for="MAX_POSTS_PER_HOUR">Max Posts Per Hour:</label>
                <input type="number" name="MAX_POSTS_PER_HOUR" value="{{ config.MAX_POSTS_PER_HOUR }}" min="1" max="60" oninput="updateBotBehavior()">
                <div class="help">Rate limit to avoid spam. <strong>MAXIMUM 60 posts/hour</strong> - bot will not start if set higher!</div>
            </div>
            
            <div class="form-group">
                <div style="background: #f0f8ff; padding: 15px; border-radius: 4px; border-left: 4px solid #007cba;">
                    <strong>📊 Bot Behavior Preview:</strong>
                    <div id="botBehavior" style="margin-top: 10px;"></div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>💬 Message Template</h2>
            <div class="form-group">
                <label for="MESSAGE_TEMPLATE">Post Message Template:</label>
                <textarea name="MESSAGE_TEMPLATE">{{ config.MESSAGE_TEMPLATE }}</textarea>
                <div class="help">
                    Available variables: {display_name}, {username}, {achievement}, {rarity}, {percentage}<br>
                    Example: 🎉 Congratulations {display_name} on earning "{achievement}"! Only {percentage}% of users have achieved this {rarity} rarity!
                </div>
            </div>
        </div>

        <div class="section">
            <h2>🔐 Admin Settings</h2>
            <div class="alert" style="background: #fff3cd; color: #856404; border: 1px solid #ffeaa7; margin-bottom: 15px;">
                <strong>Note:</strong> If you forget your login details, check the CONFIG_USERNAME and CONFIG_PASSWORD values in your .env file.
            </div>
            <div class="form-group">
                <label for="CONFIG_USERNAME">Admin Username:</label>
                <input type="text" name="CONFIG_USERNAME" value="{{ config.CONFIG_USERNAME }}">
                <div class="help">Username for accessing this config page</div>
            </div>
            <div class="form-group">
                <label for="CONFIG_PASSWORD">Admin Password:</label>
                <input type="password" name="CONFIG_PASSWORD" value="{{ config.CONFIG_PASSWORD }}">
                <div class="help">Password for accessing this config page (change from default!)</div>
            </div>
        </div>

        <button type="submit">💾 Save Configuration</button>
        <button type="button" onclick="restartBot()">🔄 Restart Bot</button>
        <button type="button" onclick="viewLogs()">📋 View Logs</button>
    </form>

    <div id="logs" style="display:none; margin-top: 20px;">
        <h3>📋 Bot Logs</h3>
        <pre id="logContent" style="background: #f5f5f5; padding: 15px; border-radius: 4px; max-height: 400px; overflow-y: auto;"></pre>
    </div>

    <script>
        function updateBotBehavior() {
            const pollInterval = parseInt(document.querySelector('input[name="POLL_INTERVAL_MINUTES"]').value) || 10;
            const maxPostsPerHour = parseInt(document.querySelector('input[name="MAX_POSTS_PER_HOUR"]').value) || 30;
            
            let behaviorText = '';
            let isError = false;
            
            // Check for validation errors
            if (pollInterval < 10) {
                behaviorText = `<span style="color: #dc3545; font-weight: bold;">❌ ERROR: Poll interval must be at least 10 minutes! Bot will not start with ${pollInterval} minutes.</span>`;
                isError = true;
            } else if (maxPostsPerHour > 60) {
                behaviorText = `<span style="color: #dc3545; font-weight: bold;">❌ ERROR: Maximum 60 posts per hour allowed! Bot will not start with ${maxPostsPerHour} posts/hour.</span>`;
                isError = true;
            } else {
                const pollsPerHour = 60 / pollInterval;
                const postsPerPoll = Math.max(1, Math.floor(maxPostsPerHour / pollsPerHour));
                
                behaviorText = `<span style="color: #28a745;">✅ Valid Configuration:</span><br>The bot will poll <strong>${pollsPerHour} times per hour</strong> (every ${pollInterval} minutes) and post up to <strong>${postsPerPoll} rarest achievements</strong> each time, for a maximum of <strong>${maxPostsPerHour} posts per hour</strong>.`;
            }
            
            const behaviorDiv = document.getElementById('botBehavior');
            behaviorDiv.innerHTML = behaviorText;
            
            // Change background color based on validation
            const parentDiv = behaviorDiv.parentElement;
            if (isError) {
                parentDiv.style.background = '#f8d7da';
                parentDiv.style.borderLeftColor = '#dc3545';
            } else {
                parentDiv.style.background = '#d4edda';
                parentDiv.style.borderLeftColor = '#28a745';
            }
        }
        
        function restartBot() {
            if (confirm('Restart the bot with new configuration?')) {
                fetch('/restart', {method: 'POST'})
                    .then(response => response.json())
                    .then(data => {
                        alert(data.message);
                        location.reload();
                    });
            }
        }

        function viewLogs() {
            const logsDiv = document.getElementById('logs');
            if (logsDiv.style.display === 'none') {
                fetch('/logs')
                    .then(response => response.text())
                    .then(data => {
                        document.getElementById('logContent').textContent = data;
                        logsDiv.style.display = 'block';
                    });
            } else {
                logsDiv.style.display = 'none';
            }
        }
        
        // Update behavior preview on page load
        document.addEventListener('DOMContentLoaded', updateBotBehavior);
    </script>
</body>
</html>
"""

@app.route('/')
@requires_auth
def index():
    config = load_config()
    return render_template_string(CONFIG_TEMPLATE, config=config)

@app.route('/', methods=['POST'])
@requires_auth
def save():
    config = {
        'FEEDMASTER_API_URL': request.form.get('FEEDMASTER_API_URL', '').strip(),
        'FEED_IDS': request.form.get('FEED_IDS', '').strip(),
        'BLUESKY_USERNAME': request.form.get('BLUESKY_USERNAME', '').strip(),
        'BLUESKY_DID': request.form.get('BLUESKY_DID', '').strip(),
        'BLUESKY_APP_PASSWORD': request.form.get('BLUESKY_APP_PASSWORD', '').strip(),
        'DISCORD_WEBHOOK_URL': request.form.get('DISCORD_WEBHOOK_URL', '').strip(),
        'MIN_RARITY_TIER': request.form.get('MIN_RARITY_TIER', 'Bronze'),
        'POLL_INTERVAL_MINUTES': request.form.get('POLL_INTERVAL_MINUTES', '10'),
        'MAX_POSTS_PER_HOUR': request.form.get('MAX_POSTS_PER_HOUR', '30'),
        'MESSAGE_TEMPLATE': request.form.get('MESSAGE_TEMPLATE', '').strip(),
        'CONFIG_USERNAME': request.form.get('CONFIG_USERNAME', 'admin').strip(),
        'CONFIG_PASSWORD': request.form.get('CONFIG_PASSWORD', 'changeme').strip()
    }
    
    # Validation
    if not config['FEEDMASTER_API_URL'] or not config['FEED_IDS']:
        flash('Feedmaster API URL and Feed IDs are required!')
        return redirect('/')
    
    # Validate that at least one platform is configured
    has_bluesky = (config['BLUESKY_USERNAME'] or config['BLUESKY_DID']) and config['BLUESKY_APP_PASSWORD']
    has_discord = config['DISCORD_WEBHOOK_URL']
    
    if not has_bluesky and not has_discord:
        flash('Configure at least one platform: Bluesky (username/DID + password) or Discord (webhook URL)!')
        return redirect('/')
    
    save_config(config)
    flash('Configuration saved successfully!')
    return redirect('/')

@app.route('/restart', methods=['POST'])
@requires_auth
def restart():
    try:
        subprocess.run(['docker', 'compose', 'restart'], check=True)
        return jsonify({'message': 'Bot restarted successfully!'})
    except subprocess.CalledProcessError:
        return jsonify({'message': 'Failed to restart bot'}), 500

@app.route('/logs')
@requires_auth
def logs():
    try:
        result = subprocess.run(['docker', 'compose', 'logs', '--tail=100', 'bluesky-bot'], 
                              capture_output=True, text=True)
        return result.stdout
    except:
        return 'Failed to fetch logs'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
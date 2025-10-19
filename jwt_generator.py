import json
import time
import asyncio
import httpx
import subprocess
import os
import requests
from typing import Dict, Optional
from datetime import datetime

# --- Settings ---
RELEASEVERSION = "OB50"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
TELEGRAM_TOKEN = "8269745816:AAE9WsQTjdkl8KN7CFDlXlJDmoEqtTJD7Wc"
TELEGRAM_CHAT_ID = "5112593221"
BRANCH_NAME = "main"
JWT_API_URL = "https://ob50-ca-jwt.vercel.app/api/token"

# --- Telegram ---
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

# --- Git Helpers ---
def run_git_command(cmd):
    try:
        result = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        return result.strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()

def detect_git_conflict():
    status = run_git_command("git status")
    return "You are not currently on a branch" in status or "both modified" in status or "Unmerged paths" in status

def resolve_git_conflict():
    print("\n⚠️ Git Conflict Detected. Please manually resolve conflicts and save files.")
    input("➡️ Press Enter once conflicts are resolved and files are saved...")
    run_git_command("git add .")
    run_git_command("git rebase --continue")
    print("✅ Rebase continued.")

def push_to_git():
    run_git_command(f"git checkout {BRANCH_NAME}")
    run_git_command(f"git push origin {BRANCH_NAME}")
    print(f"🚀 Changes pushed to {BRANCH_NAME} branch.")

def get_repo_and_filename(region):
    """Determine repository and filename based on region"""
    if region == "IND":
        return "token_ind.json"
    elif region in {"BR", "US", "SAC", "NA"}:
        return "token_br.json"
    elif region == "SG":
        return "token_bd.json"
    else:
        return "token_bd.json"

# --- Token Generation ---
async def generate_jwt_token(client, uid: str, password: str) -> Optional[Dict]:
    """Generate JWT token using the API endpoint"""
    try:
        url = f"{JWT_API_URL}?uid={uid}&password={password}"
        headers = {
            'User-Agent': USERAGENT,
            'Accept': 'application/json',
        }
        
        resp = await client.get(url, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            return {
                "token": data.get("token", ""),
                "region": data.get("region", ""),
                "server_url": data.get("server_url", "")
            }
        elif resp.status_code == 429:
            print(f"⚠️ Rate limited for UID: {uid}, waiting...")
            return "RATE_LIMITED"
        else:
            print(f"❌ API Error for {uid}: Status {resp.status_code}")
            return None
    except httpx.TimeoutException:
        print(f"⏰ Timeout for UID: {uid}")
        return "TIMEOUT"
    except Exception as e:
        print(f"❌ Exception generating token for {uid}: {str(e)}")
        return None

async def process_account_with_retry(client, index, uid, password, target_region, max_retries=5):
    for attempt in range(max_retries):
        try:
            token_data = await generate_jwt_token(client, uid, password)
            
            if token_data == "RATE_LIMITED":
                wait_time = (attempt + 1) * 30
                print(f"⏳ Rate limited for UID #{index + 1}, waiting {wait_time} seconds...")
                await asyncio.sleep(wait_time)
                continue
                
            elif token_data == "TIMEOUT":
                wait_time = (attempt + 1) * 10
                print(f"⏳ Timeout for UID #{index + 1}, waiting {wait_time} seconds...")
                await asyncio.sleep(wait_time)
                continue
            
            elif token_data and token_data.get("token"):
                api_region = token_data.get("region", "")
                
                return {
                    "serial": index + 1,
                    "uid": uid,
                    "password": password,
                    "token": token_data["token"],
                    "region": api_region,
                    "matched": api_region == target_region,
                    "attempt": attempt + 1
                }
                
        except Exception as e:
            print(f"❌ Attempt {attempt + 1} failed for UID #{index + 1}: {str(e)}")

        if attempt < max_retries - 1:
            wait_time = (attempt + 1) * 15
            print(f"🔄 UID #{index + 1} {uid} - Retry {attempt + 2}/{max_retries} after {wait_time} seconds...")
            await asyncio.sleep(wait_time)

    return {  
        "serial": index + 1,  
        "uid": uid,  
        "password": password,  
        "token": None,  
        "region": "",  
        "matched": False,
        "attempt": max_retries
    }

async def generate_tokens_for_region(region):
    start_time = time.time()

    input_file = f"uid_{region}.json"  
    if not os.path.exists(input_file):  
        print(f"⚠️ {input_file} not found.")  
        return 0

    with open(input_file, "r") as f:  
        accounts = json.load(f)  

    total_accounts = len(accounts)  
    print(f"🚀 Starting Token Generation for {region} Region using API...")  
    print(f"📊 Total accounts to process: {total_accounts}")
    print(f"🔄 Max retries per account: 5")
    print(f"⏰ Using exponential backoff for retries\n")

    # Format compatible with app.py: [{"token": "jwt_token"}, {"token": "jwt_token2"}]
    region_tokens = []  
    failed_serials = []  
    failed_values = []
    wrong_region_tokens = []
    success_attempts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    batch_size = 50
    total_batches = (total_accounts + batch_size - 1) // batch_size

    for batch_num in range(total_batches):
        start_idx = batch_num * batch_size
        end_idx = min((batch_num + 1) * batch_size, total_accounts)
        batch_accounts = accounts[start_idx:end_idx]
        
        print(f"\n🔄 Processing batch {batch_num + 1}/{total_batches} (accounts {start_idx + 1}-{end_idx})")
        
        async with httpx.AsyncClient() as client:  
            tasks = []
            for index, account in enumerate(batch_accounts):
                actual_index = start_idx + index
                tasks.append(process_account_with_retry(client, actual_index, account["uid"], account["password"], region))
            
            batch_results = await asyncio.gather(*tasks)
            
            for result in batch_results:
                serial = result["serial"]
                uid = result["uid"]
                token = result["token"]
                token_region = result.get("region", "")
                matched = result.get("matched", False)
                attempt = result.get("attempt", 1)

                if token:
                    # Save in app.py compatible format: {"token": "jwt_token"}
                    region_tokens.append({"token": token})
                    success_attempts[attempt] = success_attempts.get(attempt, 0) + 1
                    
                    if matched:
                        print(f"✅ UID #{serial} - Success on attempt {attempt} (Correct region: {token_region})")
                    else:
                        print(f"✅ UID #{serial} - Success on attempt {attempt} (Different region: {token_region})")
                        wrong_region_tokens.append({"uid": uid, "token": token, "actual_region": token_region})
                else:
                    failed_serials.append(serial)
                    failed_values.append(uid)
                    print(f"❌ UID #{serial} - Failed after {attempt} attempts")

        if batch_num < total_batches - 1:
            print("⏳ Waiting 10 seconds before next batch...")
            await asyncio.sleep(10)

    # Save tokens in app.py compatible format
    output_file = get_repo_and_filename(region)
    with open(output_file, "w") as f:  
        json.dump(region_tokens, f, indent=2)  

    if wrong_region_tokens:
        debug_file = f"wrong_region_{region}.json"
        with open(debug_file, "w") as f:
            json.dump(wrong_region_tokens, f, indent=2)
        print(f"🔍 Wrong region tokens saved to {debug_file}")

    total_time = time.time() - start_time
    minutes = int(total_time // 60)
    seconds = int(total_time % 60)

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    attempts_summary_lines = []
    for att, count in success_attempts.items():
        if count > 0:
            attempts_summary_lines.append(f"  • Attempt {att}: {count} tokens")
    attempts_summary = "\n".join(attempts_summary_lines)

    summary = (  
        f"✅ *{region} Token Generation Complete*\n\n"  
        f"🔹 *Total Tokens Generated:* {len(region_tokens)}\n"  
        f"🎯 *Correct Region Tokens:* {len(region_tokens) - len(wrong_region_tokens)}\n"  
        f"⚠️ *Different Region Tokens:* {len(wrong_region_tokens)}\n"  
        f"🔢 *Total Accounts:* {total_accounts}\n"  
        f"❌ *Failed UIDs:* {len(failed_serials)}\n"  
        f"⏱️ *Time Taken:* {minutes} minutes {seconds} seconds\n"
        f"🕒 *Time:* {current_time}\n"
        f"\n🔍 *Success by Attempt:*\n{attempts_summary}"
    )
    
    if wrong_region_tokens:
        wrong_regions = {}
        for token_data in wrong_region_tokens:
            actual_region = token_data["actual_region"]
            wrong_regions[actual_region] = wrong_regions.get(actual_region, 0) + 1
        
        region_summary_lines = []
        for reg, count in wrong_regions.items():
            region_summary_lines.append(f"  • {reg}: {count}")
        region_summary = "\n".join(region_summary_lines)
        summary += f"\n\n🌍 *Different Regions Found:*\n{region_summary}"
  
    send_telegram_message(summary)  
    print(f"\n{summary}")
    
    return len(region_tokens)

# --- Run ---
if __name__ == "__main__":
    regions = ["IND", "BD", "NA"]
    total_tokens = 0
    
    for region in regions:
        send_telegram_message(f"🤖 Dear Aditya,\n{region} Token Generation Started...⚙️")
        tokens_generated = asyncio.run(generate_tokens_for_region(region))
        total_tokens += tokens_generated
        
        if region != regions[-1]:
            print("\n⏳ Waiting 30 seconds before next region...")
            time.sleep(30)

    final_message = f"🤖 All Regions Completed!\nTotal Tokens Generated: {total_tokens}"
    send_telegram_message(final_message)

    if detect_git_conflict():  
        print("\n⚠️ Git conflict detected during previous rebase.")  
        resolve_git_conflict()  

    print("🚀 Pushing changes to Git...")  
    push_to_git()
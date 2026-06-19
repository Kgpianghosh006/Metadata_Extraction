import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def initialize_scraper_profile():
    chrome_options = Options()
    isolated_profile_dir = os.path.join(os.getcwd(), "ChromeScraperProfile")
    chrome_options.add_argument(f"user-data-dir={isolated_profile_dir}")
    
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    
    print(f"Creating/Loading profile at: {isolated_profile_dir}")
    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get("https://www.facebook.com")
        
        print("\n")
        print("ACTION REQUIRED: LOG INTO FACEBOOK")
        print("-"*50)
        print("1. A Chrome window has opened.")
        print("2. Type in your username and password.")
        print("3. Check the 'Remember Me' box if asked.")
        print("4. You have 100 seconds before the window closes.")
        print("-"*50 + "\n")
        time.sleep(100)
        
    finally:
        driver.quit()
        print("Profile saved successfully!")

if __name__ == "__main__":
    initialize_scraper_profile()
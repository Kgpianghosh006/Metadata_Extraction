import instaloader

USERNAME = input("Enter your Instagram username: ")
SESSION_ID = input("Paste your session ID from your browser: ")

def create_session():
    print(f"Attempting to inject session for {USERNAME}...")
    
    L = instaloader.Instaloader()
    
    L.context._session.cookies.set("sessionid", SESSION_ID, domain=".instagram.com")
    L.context.username = USERNAME

    try:
        profile = instaloader.Profile.from_username(L.context, USERNAME)
        print(f"Success! Authenticated as: {profile.username}")
        L.save_session_to_file()
        print("Session file generated and saved perfectly.")
        print("You can now run your metadata_scraper.py script!")
        
    except Exception as e:
        print(f"\n[ERROR] Failed to validate session: {e}")

if __name__ == "__main__":
    create_session()

"""
START HERE - WCL Reclear Tracker v1.7.0

Normal run:
    python START_HERE.py

Check setup:
    python START_HERE.py --check-settings

Reset saved WCL key:
    python START_HERE.py --reset-key

Reset saved guild:
    python START_HERE.py --reset-guild

Run with guild from command line:
    python START_HERE.py --guild "Guild Name" --realm "Realm Name" --region EU

Run and save that guild globally:
    python START_HERE.py --guild "Guild Name" --realm "Realm Name" --region EU --save-guild
"""

from main import main


if __name__ == "__main__":
    print("Starting WCL Reclear Tracker v1.7.0...")
    main()

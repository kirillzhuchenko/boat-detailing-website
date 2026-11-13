import os
import subprocess
import sys

# Step 1: initialize the database; abort if it fails
subprocess.run([sys.executable, "src/db/database.py"], check=True)

# Step 2: replace this process with the app so it receives stop signals
os.execv(sys.executable, [sys.executable, "src/backend/main.py"])
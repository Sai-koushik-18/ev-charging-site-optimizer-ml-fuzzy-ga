#!/usr/bin/env python3
import os
import sys

# Insert the src directory into the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("Importing UnifiedDataLoader...")
from data.unified_loader import UnifiedDataLoader
print("Imported successfully")

print("Creating UnifiedDataLoader instance...")
loader = UnifiedDataLoader()
print("Created successfully")

print("Test completed")
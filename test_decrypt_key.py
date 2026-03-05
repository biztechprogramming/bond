#!/usr/bin/env python3
"""Test decrypting the Google API key from SpacetimeDB."""

import os
import sys
from pathlib import Path

# Add the backend directory to the path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from backend.app.core.crypto import decrypt_value

# The encrypted key from SpacetimeDB
encrypted_key = "enc:gAAAAABpqOBrF2vsal5SXvifi99i8Grbt1xyxFa4wyc00WRuhVl-0EM_-MWt3Nbj0fVbJ2JfsTZLkEgRcJumgtVc3Xy_SrByfNEkf8Gf00EP2Ojl38GhhPasv4T056x8X64HuyQiRMaY"

print(f"Encrypted key: {encrypted_key[:50]}...")
print(f"Length: {len(encrypted_key)}")
print(f"Starts with 'enc:': {encrypted_key.startswith('enc:')}")

# Try to decrypt it
try:
    decrypted = decrypt_value(encrypted_key)
    print(f"\nDecrypted key: {decrypted[:50]}...")
    print(f"Length: {len(decrypted)}")
    print(f"Same as encrypted? {decrypted == encrypted_key}")
    print(f"Starts with 'AIza'? {decrypted.startswith('AIza')}")
    
    # Check for whitespace
    print(f"\nTrimmed key: {decrypted.strip()[:50]}...")
    print(f"Trimmed length: {len(decrypted.strip())}")
    print(f"Has leading/trailing whitespace? {decrypted != decrypted.strip()}")
    
    # Check for newlines
    print(f"\nContains newline? {'\\n' in decrypted}")
    print(f"Contains carriage return? {'\\r' in decrypted}")
    print(f"Contains tabs? {'\\t' in decrypted}")
    
except Exception as e:
    print(f"\nError decrypting: {e}")
    import traceback
    traceback.print_exc()
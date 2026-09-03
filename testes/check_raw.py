import os
from pathlib import Path

raw_dir = Path(r'D:\MarketData\mimo\RAW')
print(f'RAW directory: {raw_dir}')
print(f'Exists: {raw_dir.exists()}')

if raw_dir.exists():
    # List subdirectories
    print('\nSubdirectories:')
    for item in sorted(raw_dir.iterdir()):
        if item.is_dir():
            print(f'  {item.name}')
            
            # Count files
            files = list(item.glob('*.jsonl'))
            if files:
                total_size = sum(f.stat().st_size for f in files)
                print(f'    Files: {len(files)}, Total size: {total_size / 1024 / 1024:.2f} MB')
                
                # Show first file
                if files:
                    print(f'    First file: {files[0].name} ({files[0].stat().st_size} bytes)')

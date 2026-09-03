#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import inspect
from pathlib import Path

sys.path.insert(0, str(Path('.').resolve()))

from adapters.dashboard.handlers import DashboardHandlers

src = inspect.getsource(DashboardHandlers.handle_api_regime)
print('atr_14 in src:', 'atr_14' in src)
print('volume_relativo in src:', 'volume_relativo' in src)
print()
print('Source preview (first 1000 chars):')
print(src[:1000])

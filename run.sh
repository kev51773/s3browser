#!/usr/bin/env bash
if [ -d "venv" ]; then
    source venv/bin/activate
fi
python3 -m s3_browser.main "$@"

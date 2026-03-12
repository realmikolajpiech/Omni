#!/usr/bin/env bash

pkill -f "Omni" 2>/dev/null || true

launchctl unload ~/Library/LaunchAgents/com.omni.app.plist 2>/dev/null ||
true
rm -f ~/Library/LaunchAgents/com.omni.app.plist

rm -rf /Applications/Omni.app

rm -rf ~/Library/Application\ Support/Omni

rm -rf ~/.config/omni

rm -rf ~/.local/share/ai-memory-db
rm -rf ~/.local/share/ai-models
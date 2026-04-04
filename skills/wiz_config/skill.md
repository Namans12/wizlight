name: wiz_config

# WiZ Light Auto Config

## Description
Fetch the latest WiZ documentation from official sources and configure WiZ smart lights using the local API.

## When to Use
- When user asks to set up or configure WiZ lights
- When troubleshooting WiZ devices

## Steps
1. Fetch latest WiZ documentation
2. Extract setup/config steps
3. Suggest configuration
4. Ask for user confirmation
5. Apply changes using local API

## Tools
- fetch_docs.py
- control_light.py

## Safety
- Always confirm before controlling devices
- Use official sources only
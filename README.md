# BrowserUseDP

A browser automation project using DrissionPage for web interaction and control.

## Description

This project provides tools for browser automation, DOM manipulation, and web interaction using the DrissionPage library. It includes functionality for browser control, DOM tree building, and element interaction.

## Installation

### Prerequisites

- Python 3.12+
- uv (Python package manager)

### Setup

1. Clone the repository:
```
git clone git@github.com:Arlen1017012857/BrowserUseDP.git
cd BrowserUseDP
```

2. Create and activate a virtual environment using uv:
```
uv venv
.venv\Scripts\activate
```

3. Install dependencies:
```
uv pip install -r requirements.txt
```

## Dependencies

- drissionpage==4.1.0.18
- pocketflow==0.0.2
- python-dotenv==1.1.0
- loguru==0.7.3
- openai==1.79.0
- pyyaml==6.0.2

## Project Structure

- `browser_automation.py`: Core automation functionality
- `browser_control_agent.py`: Agent for browser control
- `build_dom_tree.js`: JavaScript for DOM tree construction (sourced from browser-use project)

## Configuration

This project uses environment variables for sensitive configuration (API keys, model endpoints, database credentials, etc.).

- `.env.example`: Template file listing all required environment variables. Copy this file to `.env` and fill in your own values.
- `.env`: Your actual configuration file (should not be committed to version control).

### Usage

1. Copy the example file:
   ```sh
   cp .env.example .env
   ```
2. Edit `.env` and fill in the required values for your environment.

**Note:** The `.env` file is listed in `.gitignore` to protect sensitive data. Never commit your real `.env` file to the repository.

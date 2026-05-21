# 🔬 PathoPilot

A lightweight, high-speed Laboratory Information System (LIS) and macro-text generator designed specifically for Pathology workflows. Built to bypass browser restrictions and integrate seamlessly with proprietary hospital systems like Diamic.

## 🚀 Current Features (Version 5)
* **Dynamic Workspaces:** Specialized protocols (e.g., Gastric Trio) with contextual UI inputs.
* **Smart Text Generation:** Translates clinical variables (inflammation, H. Pylori) into formatted, professional medical French.
* **The "Master Lock":** A stateful override system allowing the pathologist to disconnect UI sliders and manually edit the final report without losing data.
* **Rich-Text Clipboard Bypass:** Custom JavaScript injection that forces modern browsers to natively copy raw HTML (preserving bolding, italics, and layout) for direct pasting into MS Word or Diamic.
* **Settings Manager:** A database viewer for Snippets, Master Templates, and Cases.

## 🛠️ Tech Stack
* **Frontend/Backend:** Python 3, Streamlit
* **Database:** SQLite3, Pandas
* **Environment:** Proxmox LXC Container (Debian/Ubuntu)

## 💻 How to Run (Development)
This app is designed to run in an isolated Python virtual environment.

1. Navigate to the project directory:
   ```bash
   cd /root/patho_app

2. Activate the virtual environment:
   ```bash
   source venv/bin/activate

3. Boot the Stramlit server (accessible via local network):
   ```bash
   streamlit run app.py --server.address 0.0.0.0
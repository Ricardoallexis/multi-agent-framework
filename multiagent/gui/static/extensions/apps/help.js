// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the apps extension: English fallback content for registered topics.

export const HELP = {
  "apps.view": {
    "title": "Apps and agents",
    "hint": "See each model and executor status, and only the step that is missing.",
    "more": [
      "See whether each tool is installed, signed in or configured with a key, enabled, and using its permission template.",
      "Rows with a missing step open automatically. Click other rows to expand them.",
      "The app never asks for or writes secret keys. An executor starts only when you activate it."
    ]
  },
  "apps.refresh": {
    "title": "Refresh the list",
    "hint": "Read the saved connection and template status again.",
    "more": [
      "This does not use model quota or open a console.",
      "To check one tool again, use its check button."
    ]
  },
  "apps.executors": {
    "title": "Apps and executors",
    "hint": "Command-line apps and the status of their templates.",
    "more": [
      "Includes Claude, Codex, Copilot, and Antigravity (agy), with their version, sign-in, and permissions.",
      "Each app shows only the action for its first missing step."
    ]
  },
  "apps.models": {
    "title": "Models",
    "hint": "Gemini, OpenAI, and Ollama use the same layout.",
    "more": [
      "For Gemini and OpenAI, it shows the setting name in the local file, never the key value.",
      "For Ollama, it shows whether the local service responds and its version."
    ]
  },
  "apps.recheck": {
    "title": "Check again",
    "hint": "Check this tool's current status again.",
    "more": [
      "Checks installation, sign-in, local response, or quota without generating model output.",
      "Use it after installing, signing in, or restarting after a settings change."
    ]
  },
  "apps.open_console": {
    "title": "Open a console",
    "hint": "Open a visible console on this computer with the official command.",
    "more": [
      "It asks first and allows only one console at a time.",
      "Press Enter in the console to run the command. The server never runs a hidden installation.",
      "The page does not wait: it says “Console open…” and checks the status every few seconds. When you close the console, it checks again and updates the row."
    ]
  },
  "apps.probe": {
    "title": "Test the connection",
    "hint": "Make a request that does not generate text to see whether the provider responds.",
    "more": [
      "It asks Gemini or OpenAI for the model list, or checks Ollama's health. It does not use generation quota.",
      "It runs only when you click it. “Check again” does not contact Gemini or OpenAI.",
      "If a certificate check fails, the card offers a button to use system certificates."
    ]
  },
  "apps.tls_fix": {
    "title": "Provider certificates",
    "hint": "Choose which certificates Gemini, OpenAI, and Ollama use to check the connection.",
    "more": [
      "An antivirus or proxy that inspects TLS can make verification fail. With system certificates, the connection uses this computer's certificate store.",
      "This is your choice and is recorded. It applies to all providers, not executors.",
      "“Use the usual certificates” restores the certificates included with the clients. Choosing your own .pem file is an advanced setting in .local/.env."
    ]
  },
  "apps.advanced_step": {
    "title": "Advanced-mode steps",
    "hint": "This step changes a settings file, so it is available in advanced mode.",
    "more": [
      "Enabling a model and adding an API key change files on this computer.",
      "Turn on advanced mode in Customize to see the exact lines. The viewer never edits those files for you."
    ]
  },
  "apps.copy": {
    "title": "Copy lines",
    "hint": "Copy these lines to the clipboard so you can paste them yourself.",
    "more": [
      "The server never edits the catalog or local file by itself.",
      "If you choose to enable this tool, paste the lines where they belong and restart the app. Activate an executor with “Activate”."
    ]
  },
  "apps.activate": {
    "title": "Activate an executor",
    "hint": "Read a fixed notice. If you accept it, the framework can start this app.",
    "more": [
      "Only you can activate it here, one at a time. The command line, API, plans, and agents cannot do this.",
      "Activation is saved in a local setting outside the repository. The catalog stays off.",
      "You can deactivate it at any time."
    ]
  },
  "apps.activation_note": {
    "title": "Activation notice",
    "hint": "It says which app and account will be used, and what activation means.",
    "more": [
      "Each launch uses your subscription and quota. The agent can read and write in the work folder, and the provider's terms apply.",
      "The notice button is your approval. Its one-time code lasts two minutes. If it expires, click “Activate” again.",
      "“Cancel” closes the notice without activating anything."
    ]
  },
  "apps.deactivate": {
    "title": "Deactivate",
    "hint": "Turn off this executor now, without a notice.",
    "more": [
      "The framework cannot start it again until you activate it.",
      "This button does not stop anything already running. Use “Turn all off” for that."
    ]
  },
  "apps.stop_all": {
    "title": "Turn all off",
    "hint": "Stop running work and pause all executors.",
    "more": [
      "It asks first. Afterward, activate each executor again by hand, one at a time.",
      "For agy, it also writes a switch-off file. If the app did not create that file, it leaves it alone."
    ]
  },
  "apps.paused": {
    "title": "Executor paused",
    "hint": "It is paused and must be reviewed before you activate it again.",
    "more": [
      "This happens if the app path, major version, or account changed, or if you clicked “Turn all off”.",
      "“Review and activate” shows the notice again. A new minor version does not pause it."
    ]
  },
  "apps.discovery": {
    "title": "Search for apps on this computer",
    "hint": "Allow one look for the apps you have, their version and whether they have a session.",
    "more": [
      "Until you answer, this screen measures nothing and every app says “Not searched yet”.",
      "It only runs the read-only commands for the version and the session. It never installs, signs in, activates an executor, reads credentials or spends quota.",
      "Your answer is saved on this computer. You can withdraw it here at any time and the searching stops."
    ]
  },
  "apps.discovery_accept": {
    "title": "Search once",
    "hint": "Allow the search and look for the apps now.",
    "more": [
      "After this, “Refresh” and “Check again” measure again without asking.",
      "Installing an app is a separate step with its own confirmation."
    ]
  },
  "apps.discovery_decline": {
    "title": "Not now",
    "hint": "Keep the search off. Nothing is measured.",
    "more": [
      "Every app keeps saying “Not searched yet”.",
      "You can choose “Search once” later from this same card."
    ]
  },
  "apps.discovery_withdraw": {
    "title": "Withdraw consent",
    "hint": "Stop searching for apps from now on.",
    "more": [
      "Executors you already activated stay activated.",
      "The rows go back to “Not searched yet” until you allow the search again."
    ]
  },
  "apps.acp_install": {
    "title": "Install the adapter",
    "hint": "See the exact command, its version and its license.",
    "more": [
      "The app never runs the command. You copy it and run it in your own console.",
      "When it finishes, press “Check again” so the app notices the adapter."
    ]
  },
  "apps.acp_probe": {
    "title": "Check the adapter",
    "hint": "Ask the installed adapter to introduce itself.",
    "more": [
      "It only checks that the adapter is compatible. It does not activate it or sign in.",
      "Run it again after installing or changing the adapter."
    ]
  },
  "apps.acp_area": {
    "title": "Office and area",
    "hint": "Choose the area whose folder Codex ACP may work in.",
    "more": [
      "The consent and the conversation belong to the area you choose here.",
      "The server finds the office and the area from the package; this page never sends them."
    ]
  },
  "apps.acp_consent": {
    "title": "Area consent",
    "hint": "Decide once what the agent may do in this area.",
    "more": [
      "Without it, every permission request of the agent is denied.",
      "It does not activate executors, allow paid usage, or expand the working folder."
    ]
  },
  "apps.acp_write": {
    "title": "Allow file writes",
    "hint": "Let the agent write files inside this area's folder.",
    "more": [
      "Reading and searching are allowed with the consent; writing needs this switch.",
      "The agent can never write outside the folder of the area."
    ]
  },
  "apps.acp_start": {
    "title": "Start the conversation",
    "hint": "Open a live conversation with Codex ACP in this area.",
    "more": [
      "It needs the adapter checked and activated, and an area chosen.",
      "If ACP is not available, nothing switches to CLI by itself: you choose."
    ]
  },
  "apps.acp_cli": {
    "title": "Choose CLI",
    "hint": "Go to a new session and use the command-line app instead.",
    "more": [
      "CLI is the default transport and handles work without a live conversation.",
      "This link only opens the new session screen."
    ]
  },
  "apps.acp_history": {
    "title": "Saved conversations",
    "hint": "The Codex ACP conversations this device kept, newest first.",
    "more": [
      "Open one to read it. A saved conversation cannot be continued.",
      "They live in the data folder of this device, not in the area folder, and an office export does not carry them.",
      "Recognized secrets are redacted before saving, using patterns."
    ]
  },
  "apps.acp_keep_history": {
    "title": "Keep the history",
    "hint": "Choose whether the conversations of this area are saved.",
    "more": [
      "Areas marked as ordinary save by default; sensitive, local-only and unmarked areas do not until you turn it on.",
      "Changing it closes the open conversations of this area; what was saved stays.",
      "Turning it off stops saving and offers to delete what was already saved."
    ]
  },
  "apps.acp_offer_delete": {
    "title": "Delete this area's conversations",
    "hint": "Remove the conversations already saved for this area.",
    "more": [
      "Nothing is deleted unless you press this.",
      "It deletes each saved conversation of the area, one by one, and it cannot be undone."
    ]
  },
  "apps.acp_delete_all": {
    "title": "Delete all conversations",
    "hint": "Remove every saved conversation from this device.",
    "more": [
      "It asks you to confirm first, and it cannot be undone.",
      "Conversations that are open now are closed too."
    ]
  },
  "apps.agents": {
    "title": "Your own agents",
    "hint": "Declare a program that speaks ACP and use it for conversations.",
    "more": [
      "This is an advanced option. An agent you add starts disabled.",
      "You check it, activate it and remove it yourself. Nothing here runs by itself.",
      "It serves conversations in the app, not workflow steps."
    ]
  },
  "apps.agent_name": {
    "title": "Agent name",
    "hint": "The name you will see in the list and in the conversation.",
    "more": [
      "Use 1 to 60 characters on one line.",
      "The name also gives the agent its internal id, so a name already used is refused."
    ]
  },
  "apps.agent_program": {
    "title": "Program (full path)",
    "hint": "The full path of the program that speaks ACP.",
    "more": [
      "Use the program itself, not a script that needs a shell and not a folder where an area can write.",
      "If it runs on Node or Python, choose node or python and put the script's full path in the arguments."
    ]
  },
  "apps.agent_args": {
    "title": "Arguments",
    "hint": "Fixed arguments for the program, one per line.",
    "more": [
      "Arguments are literal: they cannot contain placeholders or shell syntax.",
      "The text of your tasks never goes here. It travels only inside the conversation.",
      "Empty lines at the end are ignored."
    ]
  },
  "apps.agent_add": {
    "title": "Add the agent",
    "hint": "Save the program as an agent. It starts disabled.",
    "more": [
      "Adding does not start the program or activate it.",
      "After adding, use Check and then Activate."
    ]
  },
  "apps.agent_save": {
    "title": "Save the changes",
    "hint": "Save the new name, program or arguments.",
    "more": [
      "Changing the program or its arguments pauses the activation and clears the check.",
      "Changing only the name keeps both."
    ]
  },
  "apps.agent_check": {
    "title": "Check the agent",
    "hint": "Start the program once so it introduces itself over ACP.",
    "more": [
      "This runs the program you declared, and it only sends the greeting. No conversation or task is opened.",
      "Check again after you change the program or its arguments."
    ]
  },
  "apps.agent_edit": {
    "title": "Edit the agent",
    "hint": "Change its name, program or arguments.",
    "more": [
      "Changing the program or its arguments pauses the activation and clears the check.",
      "You review it and activate it again yourself."
    ]
  },
  "apps.agent_remove": {
    "title": "Remove the agent",
    "hint": "Delete this agent after you confirm.",
    "more": [
      "It closes its open conversations and deletes its activation.",
      "Saved conversations stay until you delete them."
    ]
  },
  "apps.agent_note": {
    "title": "Activation notice",
    "hint": "It says what activating this agent means.",
    "more": [
      "The program is yours: whoever wrote it decides what it does. ACP is a protocol, not isolation.",
      "The notice button is your approval, and its one-time code lasts two minutes.",
      "“Cancel” closes the notice without activating anything."
    ]
  },
  "apps.agent_start": {
    "title": "Start a conversation",
    "hint": "Choose an area and open a live conversation with this agent.",
    "more": [
      "It needs the agent active and checked.",
      "Without the consent of the area, every permission the agent asks for is denied."
    ]
  },
  "apps.agent_area": {
    "title": "Office and area",
    "hint": "Choose the area whose folder this agent may work in.",
    "more": [
      "The consent and the conversation belong to this area.",
      "This page only opens the conversation: you write the first message there."
    ]
  }
};

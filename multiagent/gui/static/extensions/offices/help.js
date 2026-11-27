// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Contextual help for the Offices view (VOF-T01).

export const HELP = {
  "offices.view": {
    "title": "Offices",
    "hint": "See your offices, their areas, and the status of each role.",
    "more": [
      "The coordinator, teams, and their areas show the status reported by the app.",
      "Notices show what is missing and how to fix it. The list updates every four seconds.",
      "Team permissions are declarations, not guarantees of what a team can do."
    ]
  },
  "offices.plan": {
    "title": "Office plan",
    "hint": "Open the plan the coordinator shared with the areas.",
    "more": [
      "See each task, the area responsible for it, and its status.",
      "Opening the plan here does not change anything."
    ]
  },
  "offices.member_state": {
    "title": "Role status",
    "hint": "The status shows what each agent or person is doing now.",
    "more": [
      "Available means there is no active work for that role.",
      "The status changes when new data arrives."
    ]
  },
  "offices.degraded": {
    "title": "Office needs attention",
    "hint": "The notice explains what is missing and how to fix it.",
    "more": [
      "A team may need approval, or an area may need an agent.",
      "If an office changed, review the notice and import it again."
    ]
  },
  "offices.declared": {
    "title": "Declared permissions",
    "hint": "A team can declare permissions, but that does not make them effective.",
    "more": [
      "The app does not enforce these permissions while work is running.",
      "Read the team description before relying on its declarations."
    ]
  },
  "offices.levels": {
    "title": "Area level",
    "hint": "The level shows how the use of area data is limited.",
    "more": [
      "Normal and Sensitive are different levels. Local only never leaves your computer.",
      "No level set means the area has not declared a level."
    ]
  },
  "offices.import_office": {
    "title": "Import an office",
    "hint": "Choose a .zip file to review the office before installing it.",
    "more": [
      "You can also import an office again or add an area to an office you already have.",
      "In advanced mode, you can enter a local folder path. Browsers do not provide a safe folder picker.",
      "Reviewing or choosing a folder does not run office code."
    ]
  },
  "offices.card": {
    "title": "Office review card",
    "hint": "Review each area's workflows, valid YAML contracts, roles, and requirements.",
    "more": [
      "Installing also approves the teams shown on this card.",
      "A workflow without role: can run even if its team is only proposed. Workflows with roles need an approved team.",
      "The card reads functions.py and contracts.py without running them. It only recognizes static register_function calls and does not verify their contracts before approval.",
      "SkillSpector checks only a limited scope. If no scanner is installed, no scan was done; that does not mean there are no findings."
    ]
  },
  "offices.install": {
    "title": "Install office",
    "hint": "The office is installed after you review this card.",
    "more": [
      "If needed, you can allow a security scan first.",
      "Accept the risks only after reviewing what the office includes."
    ]
  },
  "offices.export": {
    "title": "Export office or area",
    "hint": "Save an office or area as a .zip file.",
    "more": [
      "The file is prepared on this computer and downloaded here.",
      "Notices show what may not work on another computer."
    ]
  },
  "offices.include_code": {
    "title": "Include code",
    "hint": "Custom code is included only when exporting in advanced mode.",
    "more": [
      "The file may contain instructions that an active agent can run.",
      "Review the code before sharing the file."
    ]
  },
  "offices.export_assistant": {
    "title": "Export with the assistant",
    "hint": "Say what to do with the office and review the package before you download it.",
    "more": [
      "A model drafts a summary from files that passed the fixed checks, with secrets and personal data masked.",
      "The checks look for known patterns and cannot promise that nothing sensitive remains. Review the package before sharing it.",
      "Nothing is downloaded until you press Download the package."
    ]
  },
  "offices.export_assistant_text": {
    "title": "Your request",
    "hint": "Write what you want to do with the office, for example who it is for or what to leave out.",
    "more": [
      "The model reads only the allowed office files, never runs, conversations, inputs, logs or databases.",
      "Drafting does not create a file."
    ]
  },
  "offices.export_assistant_cloud": {
    "title": "Remote model",
    "hint": "A remote model receives the screened office text and may cost money.",
    "more": [
      "Sending it asks the named model once. Cancel keeps the text on this computer.",
      "An office with sensitive or local-only areas never goes to a remote model."
    ]
  },
  "offices.export_assistant_change": {
    "title": "Change something",
    "hint": "Say what should change and the model drafts the package again.",
    "more": [
      "Your correction is added to the request. You can change as many times as you need.",
      "The model only suggests what to leave out; the fixed checks decide what can be packaged."
    ]
  },
  "offices.export_assistant_download": {
    "title": "Download the package",
    "hint": "This is the only button that creates the file.",
    "more": [
      "The file is rebuilt on this computer from the office as installed and checked again before it is offered.",
      "If the checks find a secret or personal data, no file is created and you see where it is."
    ]
  },
  "offices.export_assistant_code": {
    "title": "Include code in the package",
    "hint": "Custom code is included only in advanced mode and only if you accept the risks.",
    "more": [
      "The file may contain instructions that an active agent can run.",
      "Draft again after changing this option so the card matches it."
    ]
  },
  "offices.remove": {
    "title": "Remove office or area",
    "hint": "Removing it only takes it off the office list.",
    "more": [
      "Its data and saved copies are kept.",
      "You are asked to confirm before it is removed."
    ]
  },
  "offices.ask": {
    "title": "Ask the office",
    "hint": "Open New session with this office selected.",
    "more": [
      "Ask the office lets the coordinator prepare a draft plan.",
      "Ask this area opens its options in Single session.",
      "Opening this screen does not save or run anything."
    ]
  },
  "offices.privacy": {
    "title": "Office privacy",
    "hint": "Set each area's level and permission to use cloud models.",
    "more": [
      "Raising a level or removing cloud permission takes effect right away.",
      "Lowering a level or allowing cloud models requires a one-time code.",
      "Sensitive and Local only areas never use the cloud, even when permission is on."
    ]
  },
  "offices.privacy_note": {
    "title": "What this setting does not allow",
    "hint": "The note explains which protections still apply.",
    "more": [
      "The area level and cloud permission are separate settings.",
      "This setting does not approve destinations or allow data to be sent to a specific destination."
    ]
  },
  "offices.lower": {
    "title": "Lower the level",
    "hint": "This change needs one-time approval.",
    "more": [
      "The level does not change until approval is complete.",
      "Cancel rejects the approval and keeps the current level."
    ]
  },
  "offices.level_question": {
    "title": "Privacy question",
    "hint": "The first request to an area without a level asks which setting to save.",
    "more": [
      "The chosen setting is saved, and the request is tried again once.",
      "If it fails again, you see the error and are not asked again."
    ]
  }
};

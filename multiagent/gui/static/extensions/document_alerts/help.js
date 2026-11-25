// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0

export const HELP = {
  "document_alerts.view": {
    "title": "Document alerts",
    "hint": "Text from untrusted sources that was quarantined or is waiting for review.",
    "more": [
      "Each alert shows the source, the fragment that was set aside, and the policy that was applied.",
      "The original text is not shown here; it is stored separately.",
      "This screen only reports alerts. Reviews are decided in the run."
    ]
  },
  "document_alerts.card": {
    "title": "Run alerts",
    "hint": "Document alerts for the open run: source, fragment, and policy.",
    "more": [
      "An alert appears for each piece of text the detector set aside.",
      "You will see a notice here if a review needs a decision.",
      "Approve or reject from the run itself, never from this section."
    ]
  },
  "document_alerts.refresh": {
    "title": "Refresh alerts",
    "hint": "Read the alerts from the local server again.",
    "more": [
      "The list also updates automatically every few seconds.",
      "Use this after deciding a review to see its status."
    ]
  },
  "document_alerts.open_run": {
    "title": "Open run",
    "hint": "Open the run that contains this alert.",
    "more": [
      "There you can see the run's status and, if a decision is needed, the buttons to decide.",
      "This link does not approve or reject anything."
    ]
  }
};

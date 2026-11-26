// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the «Work at once» card (PAR-T04). Spanish copies live in the extension dictionary.

export const HELP = {
  "concurrency.view": {
    "title": "Work at once",
    "hint": "How many jobs run at the same time and how fast the quota is spent.",
    "more": [
      "In Auto the number comes from the quota that is left and the time until it renews.",
      "In Manual you set the number and the quota does not change it.",
      "Nothing is saved until you press Save."
    ]
  },
  "concurrency.refresh": {
    "title": "Refresh",
    "hint": "Reads the setting and the current limit again.",
    "more": [
      "Changes you have not saved are dropped.",
      "Refreshing does not change any setting."
    ]
  },
  "concurrency.save": {
    "title": "Save",
    "hint": "Saves only what you changed on this card.",
    "more": [
      "Jobs that already run finish with the setting they started with.",
      "If you lower the number nothing is cut: new jobs wait their turn."
    ]
  },
  "concurrency.selection": {
    "title": "How the number is set",
    "hint": "Auto follows the quota; Manual uses the number you type.",
    "more": [
      "Auto looks at the quota of each app and the time until it renews.",
      "With Manual the quota does not change the number."
    ]
  },
  "concurrency.manual": {
    "title": "Jobs at once",
    "hint": "The fixed number of jobs that run together, from 1 to 16.",
    "more": [
      "If there is no free place the job waits its turn and does not fail.",
      "Jobs that already run are not cut when you lower the number."
    ]
  },
  "concurrency.mode": {
    "title": "Mode",
    "hint": "Eco, Normal, Performance or Maximum: which models to use and whether spending is spread.",
    "more": [
      "Eco uses cheaper models, Normal the plan's models, Maximum only advanced ones.",
      "Eco and Normal spread the spending until the quota renews; Performance and Maximum do not.",
      "Performance and Maximum are temporary."
    ]
  },
  "concurrency.margin": {
    "title": "Margin when the quota renews",
    "hint": "How much quota should still be free when it renews.",
    "more": [
      "Each mode remembers its own margin.",
      "The minimum is 3 %, so no running step is cut in the middle."
    ]
  },
  "concurrency.duration": {
    "title": "How long it lasts",
    "hint": "When the setting goes back to Normal after Performance or Maximum.",
    "more": [
      "It can last until the next 5-hour renewal, 2 hours, or until the tasks finish.",
      "When it ends the setting goes back to Normal by itself."
    ]
  },
  "concurrency.top_tier": {
    "title": "Allow T4",
    "hint": "Lets Maximum use models of level T4.",
    "more": [
      "Without this box Maximum stops at T3.",
      "T4 is never used automatically."
    ]
  },
  "concurrency.per_app": {
    "title": "Cap per app",
    "hint": "A cap of its own for one app, besides the general one.",
    "more": [
      "Empty means no cap of its own.",
      "Between several caps the lowest always wins."
    ]
  },
  "concurrency.per_model": {
    "title": "Cap per model",
    "hint": "A cap of its own for one model, besides the general one.",
    "more": [
      "Type the model id and the cap, from 1 to 256.",
      "Removing a cap changes nothing else."
    ]
  },
  "concurrency.levels": {
    "title": "Model levels",
    "hint": "The model levels you already confirmed, read only.",
    "more": [
      "Eco and Maximum do not use a model that has no level.",
      "Proposing and confirming a level is not done from this screen yet."
    ]
  },
  "concurrency.limit_link": {
    "title": "What happens at the limit",
    "hint": "Opens Usage, where what to do at a limit is decided.",
    "more": [
      "This card does not save that decision.",
      "At a limit nothing changes by itself: you are asked."
    ]
  },
  "concurrency.budget_link": {
    "title": "Change in Usage",
    "hint": "Opens Usage, where the budget for the APIs is changed.",
    "more": [
      "This card only reads it: the budget is changed in one place.",
      "Until Usage lets you edit it, it is shown there read only."
    ]
  }
};

// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of teams and plans, their chat cards, the work map and the agent tree.
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

export const HELP = {
  "work_map.refresh": {
    "title": "Refresh the map",
    "hint": "Reload available projects and sessions.",
    "more": ["The map reads from the local server using a protected request.", "Source notices do not show private identifiers."]
  },
  "work_map.search": {
    "title": "Search the map",
    "hint": "Filter the tree and list with the same text.",
    "more": ["Each match keeps its ancestors.", "Search covers projects, tasks, sessions, and agents."]
  },
  "work_map.active": {
    "title": "Active only",
    "hint": "Show sessions and agents that are still active.",
    "more": ["The default filter includes active sessions and those with no recent activity.", "Turn it off to include finished items."]
  },
  "work_map.fold_finished": {
    "title": "Collapse finished items",
    "hint": "Collapse the children of finished items.",
    "more": ["This option is on when the view opens.", "Use the button beside an item to show or hide its children."]
  },
  "work_map.tree": {
    "title": "Tree view",
    "hint": "Group projects, stages, tasks, and sessions by relationship.",
    "more": ["Each branch has a button to collapse its children.", "Sessions show linked agents when the source provides them."]
  },
  "work_map.list": {
    "title": "List view",
    "hint": "Show the same items in rows on narrow screens.",
    "more": ["Search and the active filter are shared with the tree.", "Each row keeps its relationship level."]
  },
  "work_map.fold": {
    "title": "Collapse branch",
    "hint": "Hide or show descendant items.",
    "more": ["The button names the action and the affected branch.", "Finished items may start collapsed."]
  },
  "work_map.open_session": {
    "title": "Open session",
    "hint": "Open this session's details in the Sessions view.",
    "more": ["Navigation keeps the session ID.", "Content is reloaded from the local server."]
  },
  "work_map.assignment_edge": {
    "title": "Task assignment",
    "hint": "Open the session linked to this task.",
    "more": ["The link comes from the project's task record.", "The button works with a keyboard and keeps focus visible."]
  },
  "work_map.notice_edge": {
    "title": "Child session notice",
    "hint": "Open the child session linked to this agent.",
    "more": ["The link uses the session ID allowed by the source.", "The child's private description is not shown."]
  },

  "teams.section": {
    "title": "Teams",
    "hint": "View teams from bundles, their org charts, and their versions.",
    "more": [
      "Each team is defined in its bundle's team.yaml file and proposed from the command line or API.",
      "Only a person can approve or reject a proposal. The decision is recorded here under the operator's name."
    ]
  },
  "teams.refresh": {
    "title": "Refresh teams",
    "hint": "Reload the teams or the open team.",
    "more": [
      "The screen keeps the last response if loading fails.",
      "Refresh to see a new proposal."
    ]
  },
  "teams.list": {
    "title": "Team list",
    "hint": "Show each team with its approved version and pending proposal.",
    "more": [
      "Only bundles with a defined team are shown.",
      "Open a team by its name to see its org chart and versions, and decide on its proposal."
    ]
  },
  "teams.decision": {
    "title": "Pending proposal",
    "hint": "Show the latest proposal awaiting a decision, with options to approve or reject it.",
    "more": [
      "Approving makes it the active team version; rejecting leaves the approved version unchanged.",
      "If someone else has already decided, the server reports it and the screen reloads."
    ]
  },
  "teams.approve": {
    "title": "Approve the proposal",
    "hint": "Ask for confirmation before approving the pending proposal.",
    "more": [
      "The approved version becomes the active team version.",
      "The decision is recorded under the operator's name, with the interface channel and your note."
    ]
  },
  "teams.reject": {
    "title": "Reject the proposal",
    "hint": "Ask for confirmation before rejecting the pending proposal.",
    "more": [
      "The proposal is rejected and the current approved version stays unchanged.",
      "A rejected proposal cannot be approved later; a new version must be proposed."
    ]
  },
  "teams.note": {
    "title": "Decision note",
    "hint": "Optional text saved with the decision.",
    "more": [
      "Use it to explain why you approved or rejected the proposal.",
      "You can leave it empty."
    ]
  },
  "teams.chart": {
    "title": "Org chart",
    "hint": "Show roles in a nested list. Each role appears under the role it reports to.",
    "more": [
      "Each role shows its purpose, assignee, and status: Occupied, Vacant, or Pending approval.",
      "Pending approval marks a change in the proposal; Will be removed marks a role the proposal removes.",
      "The view warns you if the team has several roots."
    ]
  },
  "teams.links": {
    "title": "Other relationships",
    "hint": "List reviews and delegations, which are outside the hierarchy.",
    "more": [
      "Reviews shows who reviews another role's work; delegates to shows where work is passed.",
      "These do not change the tree. The hierarchy follows reporting relationships only."
    ]
  },
  "teams.mermaid": {
    "title": "Mermaid text",
    "hint": "The org chart as text, ready to paste into another tool.",
    "more": [
      "This view does not draw the diagram; it only shows the text.",
      "Paste it into an editor that supports Mermaid to see the diagram."
    ]
  },
  "teams.copy": {
    "title": "Copy Mermaid text",
    "hint": "Copy the diagram text to the clipboard.",
    "more": [
      "If the browser blocks copying, the view will tell you and you can select the text yourself.",
      "Nothing is sent outside your team."
    ]
  },
  "teams.versions": {
    "title": "Team versions",
    "hint": "List saved versions from oldest to newest.",
    "more": [
      "Each row shows its status: Draft, Proposed, Approved, or Rejected, and who decided.",
      "The active version is the latest approved version."
    ]
  },
  "teams.diff": {
    "title": "Version changes",
    "hint": "Show what changed from the approved version.",
    "more": [
      "Changes are grouped into added, removed, and changed roles, members, assignments, and relationships.",
      "If there is no approved version, everything is listed as added."
    ]
  },
  "plans.section": {
    "title": "Proposed plans",
    "hint": "Review saved plan proposals before creating tasks.",
    "more": [
      "A proposal groups suggested tasks, dependencies, and workflows for a goal.",
      "Only a person can approve or reject a proposal. The decision is recorded here under the operator's name."
    ]
  },
  "plans.refresh": {
    "title": "Refresh plans",
    "hint": "Reload the proposal list or the open proposal.",
    "more": [
      "The screen keeps the last response if loading fails.",
      "Refresh to see a proposal saved or edited through another channel."
    ]
  },
  "plans.filter": {
    "title": "Filter by status",
    "hint": "Show all proposals, or only pending, approved, or rejected ones.",
    "more": [
      "Proposed means a decision is pending; Approved means its tasks have been created.",
      "The filter checks the local server each time you change the selection."
    ]
  },
  "plans.list": {
    "title": "Proposal list",
    "hint": "Show each proposal's group, status, revision, and task count.",
    "more": [
      "Each saved change to a pending proposal increases its revision number.",
      "Open a proposal from its link to see its tasks, history, and actions."
    ]
  },
  "plans.detail": {
    "title": "Tasks and dependencies",
    "hint": "List proposal tasks, their dependencies, workflow, and preferred assignee.",
    "more": [
      "Dependencies show which tasks must finish before another can start.",
      "The preferred assignee is the suggested agent or person for a task, if one is set."
    ]
  },
  "plans.history": {
    "title": "Change history",
    "hint": "Show who saved, edited, reassigned, or decided each proposal revision.",
    "more": [
      "Each line records the date, revision number, actor, channel, and changed tasks.",
      "Optional notes entered at confirmation are saved under each action."
    ]
  },
  "plans.edit": {
    "title": "Edit tasks",
    "hint": "Change titles, goals, dependencies, workflows, or preferred assignees before approval.",
    "more": [
      "On confirmation, the full proposal is checked against the revision you are viewing.",
      "If someone changed the proposal first, the screen reports a conflict and reloads it."
    ]
  },
  "plans.reassign": {
    "title": "Reassign a task",
    "hint": "Change one task's preferred assignee, workflow, or bundle without editing other tasks.",
    "more": [
      "Leave the bundle empty if the workflow is included in the framework.",
      "Ask for confirmation before submitting. The revision increases if there is no conflict."
    ]
  },
  "plans.approve": {
    "title": "Approve the plan",
    "hint": "Ask for confirmation before approving the visible revision and creating its tasks.",
    "more": [
      "Approval creates the group if needed, creates all tasks, and starts tasks with no dependencies.",
      "The server rejects the change if the revision changed or a task with that ID already exists."
    ]
  },
  "plans.reject": {
    "title": "Reject the plan",
    "hint": "Ask for confirmation before rejecting the proposal without creating tasks.",
    "more": [
      "The proposal is closed as rejected and cannot be edited or decided on again.",
      "The decision is recorded under the operator's name with the interface channel."
    ]
  },
  "plans.advance": {
    "title": "Advance the plan",
    "hint": "Ask for confirmation, then advance this approved proposal's plan once.",
    "more": [
      "Move tasks with finished runs to review and start tasks that are ready. The view says so if there is nothing to move.",
      "The action is recorded under the operator's name with the interface channel; the plan does not advance by itself."
    ]
  },
  "plans.note": {
    "title": "Action note",
    "hint": "Optional text saved in the proposal history.",
    "more": [
      "Use it to explain why you edit, reassign, approve, or reject.",
      "You can leave this field empty."
    ]
  },

  "agent_tree.open": {
    "title": "Agent tree",
    "hint": "Open this session's agent tree.",
    "more": [
      "See who is working in the session and on what: the orchestrator and its agents, grouped by level.",
      "This view is read-only; it does not start or stop work.",
      "The view updates as the session changes."
    ]
  },
  "agent_tree.view": {
    "title": "Session tree",
    "hint": "The orchestrator and its agents, grouped by level and status.",
    "more": [
      "The orchestrator is at the top; it is the session you opened. Its agents are below, grouped from T1 to T4.",
      "Each node shows its model, level, status, and current work. Level and status are always written out, not shown by color alone.",
      "The log below records status changes since you opened this screen."
    ]
  },
  "agent_tree.back": {
    "title": "Back to session",
    "hint": "Return to this session's conversation.",
    "more": [
      "The tree does not change the session or run actions.",
      "You can reopen it from the session header."
    ]
  },
  "agent_tree.filter": {
    "title": "Active only",
    "hint": "Hide agents that have finished.",
    "more": [
      "Keep agents that are working, inactive for a while, or waiting for a usage limit or a person.",
      "Hide finished and stopped agents; the orchestrator always stays visible.",
      "Turn it off to see all agents again."
    ]
  },
  "agent_tree.node": {
    "title": "Move through the tree",
    "hint": "Use arrows to move, Enter to select, and Esc to clear.",
    "more": [
      "With focus in the tree, use Up and Down to move between nodes; Home and End go to the first and last nodes.",
      "Enter or Space selects a node and shows its details beside the tree; Esc clears the selection.",
      "You can also select a node with a mouse or touch."
    ]
  },
  "agent_tree.log": {
    "title": "Change log",
    "hint": "Agent status changes since you opened this screen.",
    "more": [
      "Records when an agent appears or changes status, using local time.",
      "Keeps only the last twelve changes and starts empty each time you open the screen.",
      "This is not the session history; see the conversation for that."
    ]
  },
  "agent_tree.open_child": {
    "title": "Open its conversation",
    "hint": "Open the agent's conversation or the run for its step.",
    "more": [
      "If the agent has its own conversation, it opens on this screen.",
      "A framework run step opens the run that contains it.",
      "This link only changes the view; it does not start or change any work."
    ]
  },

  "team_card.mode": {
    "title": "Team mode",
    "hint": "The mode used by runs with this team: supervised or autonomous.",
    "more": ["A run uses this mode unless another is chosen when it starts.", "The possible values are supervised and autonomous.", "The card says if the version does not declare a mode."]
  },
  "team_card.section": {
    "title": "Roles, rules, permissions, and model",
    "hint": "For each role: its rules, what it can do, and the model its agent would use.",
    "more": ["Rules and permissions come from the version awaiting your decision.", "The model comes from the bundle's current workflows and catalog.", "The card explains if a role has no agent or its model is missing from the catalog."]
  },
  "team_card.diff": {
    "title": "Version changes",
    "hint": "What changed from the approved version, or everything new if there is none.",
    "more": ["Includes team details, roles and their rules and permissions, members, assignments, and relationships.", "The first version is compared with an empty team."]
  },
  "team_card.approve": {
    "title": "Approve the version",
    "hint": "Ask for confirmation before approving this team version.",
    "more": ["The approved version becomes the active team version.", "The decision is recorded under the operator's name, with the interface channel and your note."]
  },
  "team_card.reject": {
    "title": "Reject the version",
    "hint": "Ask for confirmation before rejecting this team version.",
    "more": ["The version is rejected and the current approved version stays unchanged.", "A rejected version cannot be approved later; another version must be proposed."]
  },
  "team_card.note": {
    "title": "Decision note",
    "hint": "Optional text saved with the decision.",
    "more": ["Use it to explain why you approved or rejected the version.", "You can leave it empty."]
  },
  "team_card.confirm": {
    "title": "Confirm the decision",
    "hint": "Submit your decision on this version or go back without deciding.",
    "more": ["Yes submits the decision with your note.", "Back closes the confirmation without making changes."]
  },
  "team_card.open": {
    "title": "View in Teams",
    "hint": "Open the team in Teams to see its org chart and versions.",
    "more": ["You can make the same decision here or there.", "Opening it makes no changes."]
  },
  "team_card.refresh": {
    "title": "Refresh the version",
    "hint": "Reload the team version and its card.",
    "more": ["Use this if someone else has decided or the bundle catalog has changed.", "This makes no changes on the server."]
  },

  "plan_card.inputs": {
    "title": "Task inputs",
    "hint": "Show what each task in the plan receives when it runs.",
    "more": ["Long text is collapsed, and objects are summarized in readable fields.", "Inputs are shown unchanged from the plan document saved in the proposal.", "Edit them in the Plans view."]
  },
  "plan_card.approve": {
    "title": "Approve the plan",
    "hint": "Ask for confirmation before approving the plan revision shown.",
    "more": ["Tasks are created, and tasks with no dependencies start.", "The decision is recorded under the operator's name with your note.", "If the plan changed while you were viewing it, the server rejects the change and the card reloads.", "Approval is disabled if a package with roles has no approved team."]
  },
  "plan_card.reject": {
    "title": "Reject the plan",
    "hint": "Ask for confirmation before rejecting the plan.",
    "more": ["The plan is closed as rejected and no tasks are created.", "A rejected plan cannot be approved later; another must be proposed."]
  },
  "plan_card.note": {
    "title": "Decision note",
    "hint": "Optional text saved with the decision.",
    "more": ["Use it to explain why you approved or rejected the plan.", "You can leave it empty."]
  },
  "plan_card.confirm": {
    "title": "Confirm the decision",
    "hint": "Submit your decision on this plan or go back without deciding.",
    "more": ["Yes submits the decision with your note and the revision shown.", "Back closes the confirmation without making changes."]
  },
  "plan_card.team": {
    "title": "Missing team",
    "hint": "Open the proposed team version, or Teams if there is no proposal.",
    "more": ["Steps with roles need an approved team from the package.", "After approving the team, return to this plan and select Refresh."]
  },
  "plan_card.run": {
    "title": "Task run",
    "hint": "Open the run thread created for this task by the plan.",
    "more": ["See the steps, pauses, and result there.", "Opening it makes no changes."]
  },
  "plan_card.open": {
    "title": "View in Plans",
    "hint": "Open the plan in Plans to see its history and edits.",
    "more": ["You can make the same decision here or there.", "Opening it makes no changes."]
  },
  "plan_card.refresh": {
    "title": "Refresh the plan",
    "hint": "Reload the proposal, its notices, and its task runs.",
    "more": ["Use this if someone else has decided or the missing team was approved.", "This makes no changes on the server."]
  },

  "plan.auto_advance": {
    "title": "The plan advances automatically",
    "hint": "When a plan run finishes or you accept one of its tasks, waiting tasks start automatically.",
    "more": ["The system (actor system:worker) does this whenever a plan run finishes or one of its tasks is accepted, and records it here.", "If it cannot advance, the message explains why and shows «Advance the plan» so you can do it manually.", "A run recovered at startup does not advance the plan automatically; use «Advance the plan»."]
  }
};

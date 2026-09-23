CREATE TABLE `agents` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`role` text NOT NULL,
	`status` text NOT NULL,
	`model` text NOT NULL,
	`title` text,
	`brief` text,
	`workspace_path` text,
	`parent_id` text,
	`inbox_cursor` integer DEFAULT 0 NOT NULL,
	`review_round` integer DEFAULT 0 NOT NULL,
	`result_summary` text,
	`result_artifacts` text,
	`active_skills` text DEFAULT '[]' NOT NULL,
	`git_source_id` text,
	`git_branch` text,
	`git_base` text,
	`git_common_dir` text,
	`archived_at` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `agents_project_idx` ON `agents` (`project_id`);--> statement-breakpoint
CREATE TABLE `approvals` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`run_id` text NOT NULL,
	`tool_call_id` text NOT NULL,
	`tool` text NOT NULL,
	`arguments` text NOT NULL,
	`reason` text NOT NULL,
	`delegate_to_desk` integer NOT NULL,
	`status` text NOT NULL,
	`resolved_by` text,
	`note` text,
	`created_at` text NOT NULL,
	`resolved_at` text
);
--> statement-breakpoint
CREATE INDEX `approvals_project_idx` ON `approvals` (`project_id`,`status`);--> statement-breakpoint
CREATE INDEX `approvals_agent_idx` ON `approvals` (`agent_id`,`status`);--> statement-breakpoint
CREATE TABLE `artifacts` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`path` text NOT NULL,
	`title` text NOT NULL,
	`kind` text NOT NULL,
	`origin` text NOT NULL,
	`description` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `artifacts_project_idx` ON `artifacts` (`project_id`);--> statement-breakpoint
CREATE TABLE `events` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` text NOT NULL,
	`agent_id` text,
	`type` text NOT NULL,
	`payload` text NOT NULL,
	`ts` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `events_project_idx` ON `events` (`project_id`,`id`);--> statement-breakpoint
CREATE INDEX `events_agent_idx` ON `events` (`agent_id`,`id`);--> statement-breakpoint
CREATE TABLE `memory` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`kind` text NOT NULL,
	`content` text NOT NULL,
	`source` text NOT NULL,
	`supersedes` text,
	`superseded_by` text,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `memory_project_idx` ON `memory` (`project_id`,`superseded_by`);--> statement-breakpoint
CREATE TABLE `plans` (
	`project_id` text PRIMARY KEY NOT NULL,
	`items` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `projects` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`goal` text NOT NULL,
	`instructions` text NOT NULL,
	`settings` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`archived_at` text
);
--> statement-breakpoint
CREATE TABLE `sources` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`path` text NOT NULL,
	`kind` text NOT NULL,
	`label` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `sources_project_idx` ON `sources` (`project_id`);--> statement-breakpoint
CREATE TABLE `usage_totals` (
	`project_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`model` text NOT NULL,
	`day` text NOT NULL,
	`prompt_tokens` integer NOT NULL,
	`completion_tokens` integer NOT NULL,
	PRIMARY KEY(`project_id`, `agent_id`, `model`, `day`)
);

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
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `agents_project_idx` ON `agents` (`project_id`);--> statement-breakpoint
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
CREATE TABLE `projects` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`goal` text NOT NULL,
	`instructions` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `usage_totals` (
	`project_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`model` text NOT NULL,
	`day` text NOT NULL,
	`prompt_tokens` integer NOT NULL,
	`completion_tokens` integer NOT NULL,
	PRIMARY KEY(`project_id`, `agent_id`, `model`, `day`)
);

CREATE TABLE `services` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`name` text NOT NULL,
	`command` text NOT NULL,
	`cwd` text NOT NULL,
	`agent_id` text NOT NULL,
	`status` text NOT NULL,
	`pid` integer,
	`exit_code` integer,
	`exit_signal` text,
	`stop_reason` text,
	`url` text,
	`started_by` text NOT NULL,
	`started_at` text NOT NULL,
	`ended_at` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX `services_project_name_idx` ON `services` (`project_id`,`name`);
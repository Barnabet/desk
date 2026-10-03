CREATE TABLE `checks` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`title` text NOT NULL,
	`steps` text NOT NULL,
	`where` text NOT NULL,
	`expect` text NOT NULL,
	`timeout_s` integer NOT NULL,
	`cwd` text NOT NULL,
	`head` text,
	`status` text NOT NULL,
	`failed_step` integer,
	`reason` text,
	`duration_ms` integer,
	`started_at` text NOT NULL,
	`finished_at` text
);
--> statement-breakpoint
CREATE INDEX `checks_project_idx` ON `checks` (`project_id`,`status`);--> statement-breakpoint
CREATE TABLE `receipts` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`tool` text NOT NULL,
	`tool_call_id` text,
	`check_id` text,
	`step` integer,
	`command` text NOT NULL,
	`cwd` text NOT NULL,
	`head` text,
	`dirty` integer,
	`exit_code` integer,
	`outcome` text NOT NULL,
	`duration_ms` integer NOT NULL,
	`output_bytes` integer NOT NULL,
	`output_sha256` text NOT NULL,
	`started_at` text NOT NULL,
	`finished_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `receipts_agent_idx` ON `receipts` (`agent_id`,`finished_at`);--> statement-breakpoint
CREATE INDEX `receipts_head_idx` ON `receipts` (`project_id`,`head`);--> statement-breakpoint
CREATE INDEX `receipts_check_idx` ON `receipts` (`check_id`);--> statement-breakpoint
CREATE TABLE `watches` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`desk_id` text NOT NULL,
	`thread_id` text NOT NULL,
	`match` text,
	`state` text NOT NULL,
	`message_id` integer,
	`created_at` text NOT NULL,
	`ended_at` text
);
--> statement-breakpoint
CREATE INDEX `watches_open_idx` ON `watches` (`project_id`,`state`);
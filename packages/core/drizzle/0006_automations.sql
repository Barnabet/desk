CREATE TABLE `automation_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`automation_id` text NOT NULL,
	`version` integer NOT NULL,
	`trigger` text NOT NULL,
	`test` integer NOT NULL,
	`inputs` text NOT NULL,
	`by` text NOT NULL,
	`parent_run_id` text,
	`parent_step_id` text,
	`trigger_index` integer,
	`due_at` text,
	`caught_up` integer DEFAULT 0 NOT NULL,
	`status` text NOT NULL,
	`summary` text,
	`reason` text,
	`started_at` text NOT NULL,
	`finished_at` text,
	`deadline_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `automation_runs_automation_idx` ON `automation_runs` (`automation_id`,`started_at`);--> statement-breakpoint
CREATE INDEX `automation_runs_status_idx` ON `automation_runs` (`status`);--> statement-breakpoint
CREATE INDEX `automation_runs_parent_idx` ON `automation_runs` (`parent_run_id`);--> statement-breakpoint
CREATE TABLE `automation_step_runs` (
	`run_id` text NOT NULL,
	`step_id` text NOT NULL,
	`attempt` integer NOT NULL,
	`status` text NOT NULL,
	`route` text,
	`outputs` text DEFAULT '{}' NOT NULL,
	`summary` text,
	`error` text,
	`agent_id` text,
	`child_run_id` text,
	`resume_at` text,
	`gate` text,
	`question` text,
	`note` text,
	`answered_by` text,
	`started_at` text,
	`finished_at` text,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`run_id`, `step_id`)
);
--> statement-breakpoint
CREATE INDEX `automation_step_runs_status_idx` ON `automation_step_runs` (`status`);--> statement-breakpoint
CREATE TABLE `automation_versions` (
	`automation_id` text NOT NULL,
	`version` integer NOT NULL,
	`definition` text NOT NULL,
	`origin` text NOT NULL,
	`change_note` text NOT NULL,
	`via` text NOT NULL,
	`created_at` text NOT NULL,
	PRIMARY KEY(`automation_id`, `version`)
);
--> statement-breakpoint
CREATE TABLE `automations` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`name` text NOT NULL,
	`title` text NOT NULL,
	`description` text NOT NULL,
	`version` integer NOT NULL,
	`definition` text NOT NULL,
	`layout` text DEFAULT '{}' NOT NULL,
	`enabled` integer DEFAULT false NOT NULL,
	`grants` text DEFAULT '[]' NOT NULL,
	`grants_suspended` integer DEFAULT false NOT NULL,
	`grants_set_version` integer,
	`enable_request` text,
	`last_due` text DEFAULT '{}' NOT NULL,
	`deleted_at` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `automations_project_idx` ON `automations` (`project_id`,`name`);--> statement-breakpoint
ALTER TABLE `agents` ADD `automation_run_id` text;--> statement-breakpoint
ALTER TABLE `agents` ADD `automation_step_id` text;
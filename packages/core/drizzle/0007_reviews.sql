CREATE TABLE `findings` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`review_id` text NOT NULL,
	`submission_id` text NOT NULL,
	`builder_id` text NOT NULL,
	`title` text NOT NULL,
	`detail` text NOT NULL,
	`blocking` integer NOT NULL,
	`reproducer` text NOT NULL,
	`state` text NOT NULL,
	`fixed_in` text,
	`reason` text,
	`resolved_by` text,
	`created_at` text NOT NULL,
	`resolved_at` text
);
--> statement-breakpoint
CREATE INDEX `findings_builder_idx` ON `findings` (`builder_id`,`state`);--> statement-breakpoint
CREATE TABLE `reviews` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`submission_id` text NOT NULL,
	`builder_id` text NOT NULL,
	`reviewer_id` text NOT NULL,
	`criteria` text NOT NULL,
	`focus` text,
	`requested_by` text NOT NULL,
	`revealed` integer NOT NULL,
	`phase` text,
	`verdict` text,
	`requirements` text NOT NULL,
	`not_checked` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `reviews_submission_idx` ON `reviews` (`submission_id`);--> statement-breakpoint
CREATE INDEX `reviews_reviewer_idx` ON `reviews` (`reviewer_id`);--> statement-breakpoint
CREATE TABLE `submissions` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`thread_id` text NOT NULL,
	`seq` integer NOT NULL,
	`commit` text,
	`base` text,
	`artifacts` text NOT NULL,
	`claims` text NOT NULL,
	`limitations` text NOT NULL,
	`evidence` text NOT NULL,
	`superseded_by` text,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `submissions_thread_idx` ON `submissions` (`thread_id`,`seq`);--> statement-breakpoint
ALTER TABLE `agents` ADD `acceptance` text DEFAULT 'none' NOT NULL;--> statement-breakpoint
ALTER TABLE `agents` ADD `accepted_submission_id` text;--> statement-breakpoint
ALTER TABLE `agents` ADD `reviews_submission_id` text;
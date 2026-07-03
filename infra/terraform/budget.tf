# Spend guardrails — a monthly AWS Budget with email alerts and (optionally)
# an automatic EC2 stop when spend crosses the limit.
#
# Created only when `budget_alert_email` is set. cost_types excludes credits
# so the budget tracks GROSS spend: with free-plan credits the *net* bill
# reads $0 right up until the credits run out — exactly when it's too late to
# react. Set `budget_limit_usd` at or below your remaining credit balance.
#
# The auto-stop action STOPS the instance (compute billing ends) — it does not
# terminate it. The EBS volume and the now-idle Elastic IP keep costing a few
# dollars a month; run scripts/infra-down.sh for a true zero.

locals {
  create_budget = var.budget_alert_email != "" ? 1 : 0
}

resource "aws_budgets_budget" "monthly" {
  count        = local.create_budget
  name         = "${var.name_prefix}-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = [
      { threshold = 50, type = "ACTUAL" },
      { threshold = 80, type = "ACTUAL" },
      { threshold = 100, type = "FORECASTED" }, # trending to blow the month
      { threshold = 100, type = "ACTUAL" },
    ]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.budget_alert_email]
    }
  }
}

# --- auto-stop at 90% actual spend ------------------------------------------
# Budgets triggers an AWS-owned SSM automation that stops the instance; the
# role below is what that automation runs as.

locals {
  create_budget_action = local.create_budget == 1 && local.create_instance == 1 && var.budget_auto_stop ? 1 : 0
}

data "aws_iam_policy_document" "budget_assume" {
  count = local.create_budget_action

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "budget_action" {
  count = local.create_budget_action

  statement {
    sid = "RunStopAutomation"
    actions = [
      "ssm:StartAutomationExecution",
      "ssm:GetAutomationExecution",
      "ssm:StopAutomationExecution",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "StopOnlyTheAppInstance"
    actions   = ["ec2:StopInstances"]
    resources = [aws_instance.app[0].arn]
  }

  statement {
    sid       = "DescribeForAutomation"
    actions   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "budget_action" {
  count              = local.create_budget_action
  name               = "${var.name_prefix}-${var.environment}-budget-stop"
  description        = "Lets AWS Budgets stop the app instance when spend crosses the limit."
  assume_role_policy = data.aws_iam_policy_document.budget_assume[0].json
}

resource "aws_iam_role_policy" "budget_action" {
  count  = local.create_budget_action
  name   = "stop-app-instance"
  role   = aws_iam_role.budget_action[0].id
  policy = data.aws_iam_policy_document.budget_action[0].json
}

resource "aws_budgets_budget_action" "stop_ec2" {
  count              = local.create_budget_action
  budget_name        = aws_budgets_budget.monthly[0].name
  action_type        = "RUN_SSM_DOCUMENTS"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action[0].arn

  # 90% ACTUAL: stop while there's still headroom under the limit, since cost
  # data lags by up to a day.
  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 90
  }

  definition {
    ssm_action_definition {
      action_sub_type = "STOP_EC2_INSTANCES"
      instance_ids    = [aws_instance.app[0].id]
      region          = var.region
    }
  }

  subscriber {
    address           = var.budget_alert_email
    subscription_type = "EMAIL"
  }
}

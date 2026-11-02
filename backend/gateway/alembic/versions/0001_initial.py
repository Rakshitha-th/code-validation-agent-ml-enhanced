"""Create project and analysis persistence tables."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None
ANALYSIS_RUN_ID_FOREIGN_KEY = "analysis_runs.run_id"


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "project_files",
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "path"),
    )
    op.create_table(
        "analysis_runs",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("workflow_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.String(80), nullable=True),
        sa.Column("validation_status", sa.String(32), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("project_context", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_analysis_runs_project_id", "analysis_runs", ["project_id"])
    op.create_index("ix_analysis_runs_workflow_id", "analysis_runs", ["workflow_id"])
    op.create_table(
        "findings",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("finding_id", sa.String(128), nullable=False),
        sa.Column("agent", sa.String(80), nullable=True),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=True),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], [ANALYSIS_RUN_ID_FOREIGN_KEY], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "finding_id"),
    )
    op.create_table(
        "tool_results",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], [ANALYSIS_RUN_ID_FOREIGN_KEY], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "tool_name"),
    )
    op.create_table(
        "workflow_events",
        sa.Column("event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], [ANALYSIS_RUN_ID_FOREIGN_KEY], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_workflow_events_run_id", "workflow_events", ["run_id"])
    op.create_table(
        "repairs",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], [ANALYSIS_RUN_ID_FOREIGN_KEY], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "iteration"),
    )
    op.create_table(
        "validation_results",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], [ANALYSIS_RUN_ID_FOREIGN_KEY], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "reports",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], [ANALYSIS_RUN_ID_FOREIGN_KEY], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("validation_results")
    op.drop_table("repairs")
    op.drop_index("ix_workflow_events_run_id", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_table("tool_results")
    op.drop_table("findings")
    op.drop_index("ix_analysis_runs_workflow_id", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_project_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_table("project_files")
    op.drop_table("projects")
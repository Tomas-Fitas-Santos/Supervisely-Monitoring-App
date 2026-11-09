"""Configuration of one pre-existing native Supervisely team and source workspace.

DEMO and PRODUCTION use identical binding, identity and invitation contracts.
No method in this module creates a native team or workspace.
"""
from sqlalchemy import select

from .db import (
    Audit, Batch, EventConfig, EventInvitee, EventMember, EventPreferences,
    Registration, SharedInvitation, SourceOrigin, Team, now,
)
from .service import WorkflowError


def positive_id(value, name):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise WorkflowError(f'Enter a positive {name}.') from None
    if number <= 0 or str(number) != str(value).strip():
        raise WorkflowError(f'Enter a positive {name}.')
    return number


def single_team(config):
    return bool(
        config and config.monitoring_team_id
        and config.annotator_team_id == config.monitoring_team_id
    )


class EventSettings:
    def __init__(self, sessions):
        self.sessions = sessions

    def snapshot(self, current_user_id=None):
        with self.sessions() as session:
            config = session.get(EventConfig, 1)
            prefs = session.get(EventPreferences, 1)
            return {
                'user_id': int(current_user_id) if current_user_id else None,
                'active_local_user_id': prefs.active_local_user_id if prefs else None,
                'active_local_login': prefs.active_local_login if prefs else None,
                'organiser_user_id': config.owner_id,
                'server_address': config.server_address,
                'team_id': config.monitoring_team_id if single_team(config) else None,
                'workspace_id': config.source_workspace_id if single_team(config) else None,
                'single_team': single_team(config),
                'revision': prefs.revision if prefs else 0,
                'membership_synced_at': prefs.membership_synced_at if prefs else None,
            }

    def bind(self, gateway, actor, team_id, workspace_id, expected_revision,
             launch_team_id=None):
        """Bind only pre-existing resources, once or before event data exists."""
        team_id = positive_id(team_id, 'Team ID')
        workspace_id = positive_id(workspace_id, 'Workspace ID')
        try:
            revision = int(expected_revision)
        except (TypeError, ValueError):
            raise WorkflowError('Reload Settings before saving.') from None
        with self.sessions() as session:
            config = session.get(EventConfig, 1)
            prefs = session.get(EventPreferences, 1)
            if config.owner_id != int(actor):
                raise WorkflowError('Only the event organiser can change shared resource IDs.')
            if prefs.revision != revision:
                raise WorkflowError('Settings were changed elsewhere. Reload and review.')
            if config.monitoring_team_id and not single_team(config):
                raise WorkflowError(
                    'This database has legacy multi-team bindings. It must be '
                    'migrated explicitly, not silently repointed.'
                )
            changing = config.monitoring_team_id and config.monitoring_team_id != team_id
            if changing:
                has_work = (
                    session.scalar(select(Registration.id).limit(1)) is not None
                    or session.scalar(select(Team.id).limit(1)) is not None
                    or session.scalar(select(Batch.id).limit(1)) is not None
                    or session.scalar(select(SharedInvitation.email).limit(1)) is not None
                )
                if has_work:
                    raise WorkflowError(
                        'Cannot change the event Team ID while roster, invitations '
                        'or tasks exist. Create a new isolated event instead.'
                    )
            if (config.source_workspace_id and config.source_workspace_id != workspace_id
                    and (session.scalar(select(SourceOrigin.source_id).limit(1))
                         is not None
                         or session.scalar(select(Batch.id).limit(1)) is not None)):
                raise WorkflowError(
                    'The source workspace cannot be changed after dataset import/distribution.'
                )
        if launch_team_id and int(launch_team_id) != team_id:
            raise WorkflowError(
                'The hosted app must be launched from the configured event team.'
            )

        try:
            team = gateway.api.team.get_info_by_id(team_id)
            if not team or int(team.id) != team_id:
                raise ValueError()
        except Exception:
            raise WorkflowError('Could not access the existing Supervisely Team ID.') from None
        from .setup_gateway import SetupGateway
        SetupGateway(gateway.api).organiser(team_id, int(actor))
        try:
            workspaces = gateway.api.workspace.get_list(team_id)
            matching = [w for w in workspaces if int(w.id) == workspace_id]
        except Exception:
            raise WorkflowError(
                'Could not list workspaces in the selected Supervisely team.'
            ) from None
        if not matching:
            raise WorkflowError(
                'The Workspace ID does not belong to the selected team, '
                'or this account cannot access it.'
            )

        with self.sessions.begin() as session:
            config = session.get(EventConfig, 1)
            prefs = session.get(EventPreferences, 1)
            if prefs.revision != revision or config.owner_id != int(actor):
                raise WorkflowError('Settings changed during validation. Reload before saving.')
            if config.monitoring_team_id and config.monitoring_team_id != team_id:
                if session.scalar(select(Registration.id).limit(1)) is not None:
                    raise WorkflowError('The roster changed while updating Settings.')
            old_team, old_workspace = config.monitoring_team_id, config.source_workspace_id
            config.monitoring_team_id = team_id
            # Equal IDs explicitly signal our new single-native-team architecture.
            config.annotator_team_id = team_id
            config.source_workspace_id = workspace_id
            prefs.revision += 1
            prefs.membership_synced_at = None
            session.add(Audit(
                team_id=team_id, batch_id='', actor_id=int(actor),
                action='event_binding_saved',
                details={'old_team_id': old_team, 'team_id': team_id,
                         'old_workspace_id': old_workspace, 'workspace_id': workspace_id}
            ))
        return self.snapshot(actor)

    def add_monitor(self, gateway, actor, user_id, demo_dual_role=False):
        user_id = positive_id(user_id, 'Monitor User ID')
        with self.sessions() as session:
            config = session.get(EventConfig, 1)
            if not single_team(config):
                raise WorkflowError('Configure the existing event Team ID first.')
            if config.owner_id != int(actor):
                raise WorkflowError('Only the organiser can approve monitoring access.')
            team_id = config.monitoring_team_id
        try:
            member = gateway.api.user.get_member_info_by_id(team_id, user_id)
        except Exception:
            raise WorkflowError(
                'The user must accept an invitation to the event team before '
                'being registered as a monitor.'
            ) from None
        if not member or getattr(member, 'disabled', False):
            raise WorkflowError('Monitor is not an active member of the event team.')
        role = str(getattr(member, 'role', '') or '').lower()
        # The CSV and native invitation reconciliation define who may monitor.
        # A reviewer is eligible ONLY when the roster explicitly declares
        # participant_monitor, preserving the ability to annotate assigned work.
        with self.sessions() as session:
            invitation = session.scalar(select(SharedInvitation).where(
                SharedInvitation.team_id == team_id,
                SharedInvitation.user_id == user_id,
                SharedInvitation.status == 'accepted',
            ))
            target = session.get(EventInvitee, invitation.email) if invitation else None
            if not target or target.event_role not in ('monitor', 'participant_monitor'):
                raise WorkflowError(
                    'This user is not an accepted CSV-declared monitor. '
                    'Import the role-aware roster and refresh memberships first.'
                )
            if role != target.native_role and not (
                    role == 'admin' and user_id == config.owner_id):
                raise WorkflowError(
                    'The native Supervisely role does not match the CSV '
                    'monitor role. Review it in Supervisely before registering.'
                )
        if demo_dual_role:
            if target.event_role != 'participant_monitor':
                raise WorkflowError('Dual-role monitoring requires participant_monitor in the CSV.')
        login = str(getattr(member, 'login', '') or '').strip()
        if not login:
            raise WorkflowError('Cannot verify native account login for this User ID.')
        name = str(getattr(member, 'name', '') or login)
        with self.sessions.begin() as session:
            stored = session.get(EventMember, user_id)
            if stored:
                stored.group = 'monitors'
                stored.login, stored.name = login, name
            else:
                session.add(EventMember(
                    user_id=user_id, group='monitors', login=login, name=name
                ))
            session.add(Audit(team_id=team_id, batch_id='', actor_id=int(actor),
                action='monitor_authorised',
                details={'monitor_user_id': user_id, 'native_role': role,
                         'demo_dual_role': bool(demo_dual_role)}))
        return {'id': user_id, 'login': login, 'name': name}

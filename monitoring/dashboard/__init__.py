from supervisely.app import DataJson
from supervisely.app.widgets_context import JinjaWidgets
from supervisely.app.widgets import Widget


class Dashboard(Widget):
    def __init__(self):
        super().__init__(widget_id='dashboard', file_path=__file__)
        JinjaWidgets().context['__widget_scripts__']['Nightjar'] = './static/dashboard.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarSetup'] = './static/setup.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarConnection'] = './static/connection.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarCompetition'] = './static/competition.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarAttendance'] = './static/attendance.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarSettings'] = './static/settings.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarStructuredDataset'] = './static/structured_dataset.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarJobPlanner'] = './static/job_planner.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarLogistics'] = './static/event_logistics.js'

    def get_json_data(self):
        return {'snapshot': {'teams': []}, 'message': '', 'error': False, 'links': [],
                'connected': False, 'directory': {}, 'sources': {}, 'review_preview': None,
                'competition': {}, 'registration_rows': [], 'attendance': {'teams': [], 'monitors': [], 'summary': {}},
                'roster_plan_id': None, 'roster_balance_preview': None, 'event_settings': {},
                'logistics': {'teams': [], 'summary': {}},
                'prepared_jobs': [], 'media_preview': None, 'job_plan_id': None, 'job_preview': None,
                'unit_page': {}, 'leaderboard': [], 'simulator': False, 'pilot': False,
                'export_data': None, 'closed': False,
                'inventory': [],
                'can_setup': False, 'setup': {}, 'catalog': {}, 'upload_offset': 0, 'plan_id': None,
                'preview': None, 'assignment_plan_id': None, 'assignment_preview': [],
                'connection': {'local': False, 'connected': False, 'groups_ready': False,
                               'server_address': 'https://app.supervisely.com', 'login': ''}}

    def get_json_state(self):
        return {'action': 'refresh'}

    def display(self, snapshot, message='', error=False, links=None):
        DataJson()[self.widget_id].update(snapshot=snapshot, message=message, error=error,
                                        links=links or [])
        DataJson().send_changes()

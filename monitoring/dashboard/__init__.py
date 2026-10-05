from supervisely.app import DataJson
from supervisely.app.widgets_context import JinjaWidgets
from supervisely.app.widgets import Widget


class Dashboard(Widget):
    def __init__(self):
        super().__init__(widget_id='dashboard', file_path=__file__)
        JinjaWidgets().context['__widget_scripts__']['Nightjar'] = './static/dashboard.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarSetup'] = './static/setup.js'
        JinjaWidgets().context['__widget_scripts__']['NightjarConnection'] = './static/connection.js'

    def get_json_data(self):
        return {'snapshot': {'teams': []}, 'message': '', 'error': False, 'links': [],
                'connected': False, 'directory': {}, 'sources': {}, 'review_preview': None,
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

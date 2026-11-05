"""Excel and CSV share the same registration column mapping contract."""
import base64
from io import BytesIO

import pytest
from openpyxl import Workbook

from monitoring.excel_import import excel_to_csv
from monitoring.registration import Registrations
from monitoring.service import WorkflowError


def workbook_bytes(rows, second_sheet=False):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Inscrições'
    for row in rows:
        sheet.append(row)
    if second_sheet:
        sheet2 = book.create_sheet('Outra folha')
        sheet2.append(['Info', 'Detalhe'])
        sheet2.append(['Teste', '2'])
    stream = BytesIO()
    book.save(stream)
    book.close()
    return base64.b64encode(stream.getvalue()).decode()


def test_portuguese_excel_headers_go_through_existing_importer(service):
    encoded = workbook_bytes([
        ['Equipa', 'Primeiro participante', 'Segundo participante'],
        ['Passarinhos', 'one@example.test', 'two@example.test'],
    ])
    result = excel_to_csv(encoded)
    assert result['sheet_name'] == 'Inscrições'
    assert result['headers'] == [
        'Equipa', 'Primeiro participante', 'Segundo participante'
    ]
    assert Registrations(service.sessions).import_csv(
        90, result['csv_text'], {
            'team_name': 'Equipa',
            'member_1_email': 'Primeiro participante',
            'member_2_email': 'Segundo participante',
        }
    ) == 1
    assert Registrations(service.sessions).rows()[0]['team_name'] == 'Passarinhos'


def test_select_worksheet_and_header_line():
    encoded = workbook_bytes([
        ['Inscrições do evento', '', ''],
        ['Team', 'Member A', 'Member B'],
        ['Pair 1', 'a@example.test', 'b@example.test']
    ], second_sheet=True)
    result = excel_to_csv(encoded, 'Inscrições', 2)
    assert result['sheets'] == ['Inscrições', 'Outra folha']
    assert result['headers'] == ['Team', 'Member A', 'Member B']
    assert 'Pair 1' in result['csv_text']


@pytest.mark.parametrize('encoded', ['', 'abc-not-base64', base64.b64encode(b'not a zip').decode()])
def test_invalid_excel_rejected(encoded):
    with pytest.raises(WorkflowError):
        excel_to_csv(encoded)


def test_ambiguous_excel_headers_rejected():
    encoded = workbook_bytes([
        ['Equipa', 'Email', 'Email'],
        ['Pair 1', 'a@example.test', 'b@example.test']
    ])
    with pytest.raises(WorkflowError, match='unique'):
        excel_to_csv(encoded)


def test_one_member_excel_still_requires_event_pair_review(service):
    encoded = workbook_bytes([
        ['Equipa', 'Primeiro participante', 'Segundo participante'],
        ['One member', 'one@example.test', '']
    ])
    converted = excel_to_csv(encoded)
    assert len(converted['headers']) == 3
    Registrations(service.sessions).import_csv(90, converted['csv_text'], {
        'team_name': 'Equipa',
        'member_1_email': 'Primeiro participante',
        'member_2_email': 'Segundo participante'
    })
    from monitoring.attendance import EventRoster
    view = EventRoster(service.sessions).overview(90, organiser=True)
    assert view['teams'][0]['arrival_status'] == 'incomplete_registration'

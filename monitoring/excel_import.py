"""Bounded Excel registration upload, preserving the existing CSV mapping workflow.

Both DEMO and PRODUCTION can import Excel workbooks with identical headers.
This module only reads data; it never modifies the source workbook.
"""
import base64
import binascii
import csv
import io
from datetime import date, datetime
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from .service import WorkflowError

MAX_XLSX_BYTES = 3 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
MAX_SHEETS = 20
MAX_ROWS = 10001
MAX_COLS = 100


def excel_to_csv(encoded, sheet_name=None, header_row=1):
    """Convert a selected worksheet to the existing UTF-8 CSV import contract."""
    if not isinstance(encoded, str) or len(encoded) > (MAX_XLSX_BYTES * 4 // 3 + 16):
        raise WorkflowError("Choose an Excel workbook smaller than 3 MiB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise WorkflowError("Invalid Excel upload encoding.") from None
    if not raw or len(raw) > MAX_XLSX_BYTES:
        raise WorkflowError("Choose an Excel workbook smaller than 3 MiB.")
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) > 4000 or sum(
                    item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise WorkflowError("Workbook contains too much uncompressed data.")
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True,
                                 keep_links=False)
    except WorkflowError:
        raise
    except (BadZipFile, ValueError, OSError, KeyError):
        raise WorkflowError("Could not open the .xlsx workbook.") from None
    try:
        sheet_names = [sheet.title for sheet in workbook.worksheets
                       if sheet.sheet_state == 'visible']
        if len(sheet_names) > MAX_SHEETS:
            raise WorkflowError("Too many visible worksheets.")
        if not sheet_names:
            raise WorkflowError("The Excel workbook has no visible sheets.")
        if sheet_name is not None and sheet_name not in sheet_names:
            raise WorkflowError("Selected worksheet does not exist.")
        worksheet = workbook[sheet_name or sheet_names[0]]
        try:
            start = int(header_row)
        except (TypeError, ValueError):
            raise WorkflowError("Invalid header row number.") from None
        if start < 1 or start > 30:
            raise WorkflowError("Choose a header row between 1 and 30.")
        if worksheet.max_column and worksheet.max_column > MAX_COLS:
            raise WorkflowError("Too many worksheet columns.")
        if worksheet.max_row and worksheet.max_row > MAX_ROWS + start - 1:
            raise WorkflowError("Too many worksheet rows.")

        def string(value):
            if value is None:
                return ''
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            if isinstance(value, bool):
                return 'TRUE' if value else 'FALSE'
            return str(value).strip()

        rows = []
        for i, cells in enumerate(
                worksheet.iter_rows(min_row=start, max_col=MAX_COLS, values_only=True), start):
            if i - start >= MAX_ROWS:
                raise WorkflowError("Excel registrations exceed the 10,000-row limit.")
            record = [string(v) for v in cells]
            while record and not record[-1]:
                record.pop()
            if record:
                rows.append(record)
        if len(rows) < 2:
            raise WorkflowError("Worksheet must have a header and registration rows.")
        headers = rows[0]
        if any(not h for h in headers) or len(set(headers)) != len(headers):
            raise WorkflowError("Excel column headers must be nonempty and unique.")
        if len(headers) > MAX_COLS:
            raise WorkflowError("Too many Excel columns.")
        writer_buffer = io.StringIO(newline='')
        writer = csv.writer(writer_buffer, lineterminator='\n')
        writer.writerow(headers)
        for row in rows[1:]:
            if any(value for value in row):
                writer.writerow((row + [''] * len(headers))[:len(headers)])
        csv_text = writer_buffer.getvalue()
        if len(csv_text.encode('utf-8')) > 2 * 1024 * 1024:
            raise WorkflowError("Converted registrations exceed the 2 MiB CSV limit.")
        return {
            "csv_text": csv_text,
            "headers": headers,
            "sheet_name": worksheet.title,
            "sheets": sheet_names,
            "header_row": start,
        }
    finally:
        workbook.close()

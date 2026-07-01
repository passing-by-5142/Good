const CONFIG = {
  TOKEN: 'make_a_long_random_token',
  SHEET_NAME: 'snapshot',
  REPORT_SHEET_NAME: 'reports',
};

function doGet() {
  return jsonResponse({
    ok: true,
    message: 'KIS snapshot web app is reachable. Use POST to append rows.',
  }, 200);
}

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);
    if (payload.token !== CONFIG.TOKEN) {
      return jsonResponse({ ok: false, error: 'unauthorized' }, 401);
    }

    const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = getOrCreateSheet_(spreadsheet, CONFIG.SHEET_NAME);
    ensureHeader_(sheet);
    const reportSheet = getOrCreateSheet_(spreadsheet, CONFIG.REPORT_SHEET_NAME);
    ensureReportHeader_(reportSheet);

    const capturedAt = payload.captured_at || new Date().toISOString();
    const source = payload.source || 'KIS';
    const rows = (payload.items || []).map((item) => [
      capturedAt,
      source,
      item.ticker || '',
      item.name || '',
      item.quantity || '',
      item.cost_basis || '',
      item.avg_buy_price || '',
      item.price || '',
      item.change || '',
      item.change_pct || '',
      item.market_value || '',
      item.unrealized_pnl || '',
      item.unrealized_pnl_pct || '',
      item.volume || '',
      item.open || '',
      item.high || '',
      item.low || '',
    ]);

    if (rows.length > 0) {
      sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
    }

    let reportInserted = 0;
    if (payload.report) {
      const report = payload.report;
      reportSheet.appendRow([
        new Date(),
        report.captured_at || capturedAt,
        report.report_type || '',
        report.summary || '',
        report.action || '',
        valueOrBlank_(report.total_market_value),
        valueOrBlank_(report.total_cost_basis),
        valueOrBlank_(report.total_unrealized_pnl),
        valueOrBlank_(report.total_unrealized_pnl_pct),
        report.markdown || '',
      ]);
      reportInserted = 1;
    }

    return jsonResponse({ ok: true, inserted: rows.length, report_inserted: reportInserted }, 200);
  } catch (err) {
    return jsonResponse({ ok: false, error: String(err) }, 500);
  }
}

function getOrCreateSheet_(spreadsheet, name) {
  return spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name);
}

function valueOrBlank_(value) {
  return value === null || value === undefined ? '' : value;
}

function ensureHeader_(sheet) {
  const header = [
    'captured_at',
    'source',
    'ticker',
    'name',
    'quantity',
    'cost_basis',
    'avg_buy_price',
    'price',
    'change',
    'change_pct',
    'market_value',
    'unrealized_pnl',
    'unrealized_pnl_pct',
    'volume',
    'open',
    'high',
    'low',
  ];
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(header);
    return;
  }
  const firstRow = sheet.getRange(1, 1, 1, header.length).getValues()[0];
  const headerChanged = header.some((value, index) => firstRow[index] !== value);
  if (firstRow[0] !== 'captured_at') {
    sheet.insertRowBefore(1);
    sheet.getRange(1, 1, 1, header.length).setValues([header]);
  } else if (headerChanged) {
    sheet.getRange(1, 1, 1, header.length).setValues([header]);
  }
}

function ensureReportHeader_(sheet) {
  const header = [
    'created_at',
    'captured_at',
    'report_type',
    'summary',
    'action',
    'total_market_value',
    'total_cost_basis',
    'total_unrealized_pnl',
    'total_unrealized_pnl_pct',
    'markdown',
  ];
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(header);
    return;
  }
  const firstRow = sheet.getRange(1, 1, 1, header.length).getValues()[0];
  const headerChanged = header.some((value, index) => firstRow[index] !== value);
  if (firstRow[0] !== 'created_at') {
    sheet.insertRowBefore(1);
    sheet.getRange(1, 1, 1, header.length).setValues([header]);
  } else if (headerChanged) {
    sheet.getRange(1, 1, 1, header.length).setValues([header]);
  }
}

function jsonResponse(body, statusCode) {
  return ContentService
    .createTextOutput(JSON.stringify({ statusCode, ...body }))
    .setMimeType(ContentService.MimeType.JSON);
}

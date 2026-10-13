// Проверка Prepare в Node: настоящий интерфейс DataLens этим не имитируется.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const plan = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const data = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const codes = Object.fromEntries(plan.requests.filter(r => r.key).map(r => [r.key, r.body.entry.data.prepare]));
let executions = 0;
const schemasChecked = {markdown: 0, table: 0, gravity_charts: 0};
const kinds = Object.fromEntries(data.config.charts.map(c => [c.key, c.kind]));
const gravityResults = [];
function text(value) {assert.equal(typeof value, 'string'); assert.ok(value.trim());}
function style(value = {}) {
    const allowed = ['fontSize', 'fontWeight', 'fontColor'];
    assert.ok(Object.keys(value).every(k => allowed.includes(k)), 'Неверное поле BaseTextStyle');
    if (value.fontSize !== undefined) assert.match(value.fontSize, /^\d+px$/);
    assert.equal(value.fontColor, undefined, 'Текст должен наследовать тему');
}
function schemaCheck(kind, result) {
    if (kind === 'markdown') {
        text(result.markdown);
        assert.deepEqual(Object.keys(result), ['markdown']);
        assert.ok(!/<(?:div|span|style)\b/.test(result.markdown), 'Raw HTML в Markdown не поддерживается');
    } else if (kind === 'table') {
        assert.ok(Array.isArray(result.head) && result.head.length > 0);
        assert.ok(Array.isArray(result.rows));
        assert.equal(new Set(result.head.map(h => h.id)).size, result.head.length);
        for (const h of result.head) {
            text(h.id); text(h.name);
            assert.ok(['text', 'number'].includes(h.type));
            assert.ok(h.width > 0);
            if (h.type === 'number') assert.ok(Number.isInteger(h.formatter.precision));
            assert.equal(h.css.color, undefined);
        }
        for (const row of result.rows) {
            assert.equal(row.cells.length, result.head.length);
            for (const [i, cell] of row.cells.entries()) {
                assert.ok(['string', 'number'].includes(typeof cell.value));
                if ((cell.type || result.head[i].type) === 'number') assert.ok(Number.isFinite(cell.value));
                assert.equal(cell.css.color, undefined);
            }
        }
    } else {
        text(result.title.text); style(result.title.style);
        assert.ok(result.title.maxRowCount >= 2);
        assert.equal(result.xAxis.type, 'linear');
        assert.equal(result.xAxis.visible, true); assert.equal(result.xAxis.labels.enabled, true);
        text(result.xAxis.title.text); style(result.xAxis.title.style); style(result.xAxis.labels.style);
        assert.equal(result.yAxis.length, 1);
        const axis = result.yAxis[0];
        assert.equal(axis.type, 'category'); assert.equal(axis.visible, true);
        assert.equal(axis.labels.enabled, true); text(axis.title.text); style(axis.labels.style);
        assert.ok(Array.isArray(axis.categories));
        assert.equal(new Set(axis.categories).size, axis.categories.length);
        for (const series of result.series.data) {
            assert.ok(['bar-y', 'line', 'scatter'].includes(series.type)); text(series.name);
            assert.ok(Array.isArray(series.data));
            assert.equal(typeof series.dataLabels.enabled, 'boolean'); style(series.dataLabels.style);
            for (const point of series.data) {
                assert.ok(Number.isFinite(point.x));
                assert.ok(axis.categories.includes(point.y), 'Категория точки отсутствует на оси');
                if (series.dataLabels.enabled) text(point.label);
            }
        }
        if (result.legend.enabled) style(result.legend.itemStyle);
    }
}
function run(key, params = {}) {
    const sandbox = {module: {exports: {}}, Editor: {getParams: () => params}};
    vm.runInNewContext(codes[key], sandbox, {timeout: 2000});
    executions++;
    const result = JSON.parse(JSON.stringify(sandbox.module.exports));
    schemaCheck(kinds[key], result);
    schemasChecked[kinds[key]]++;
    if (kinds[key] === 'gravity_charts' && process.argv[4]) gravityResults.push({key, params, result});
    return result;
}
function near(a, b) {assert.ok(Math.abs(a - b) <= 1e-10 * Math.max(1, Math.abs(b)), `${a} != ${b}`);}
function field(row, result, id) {return row.cells[result.head.findIndex(h => h.id === id)];}

for (const key of Object.keys(codes)) {
    const result = run(key);
    assert.ok(result.markdown !== undefined || result.rows !== undefined || result.series !== undefined, key);
}
for (const rid of ['all', ...data.recipes.map(r => r.recipe_id)]) {
    const params = {recipe_id: [rid]};
    assert.ok(run('kpi_max', params).markdown.includes('35,11%'));
    assert.ok(run('kpi_control', params).markdown.includes('29,45'));
    assert.ok(run('kpi_variants', params).markdown.includes('4 варианта'));
    const force = run('force_graph', params);
    const parties = force.series.data.find(s => s.name.startsWith('Три партийных'));
    const means = force.series.data.find(s => s.name === 'Среднее рецептуры');
    const ids = rid === 'all' ? data.recipes.map(r => r.recipe_id) : [...new Set(['control', rid])];
    assert.equal(parties.data.length, ids.length * 3);
    assert.equal(means.data.length, ids.length);
    const expectedForce = data.physical.filter(r => r.metric_id === 'breaking_strength_n' && ids.includes(r.recipe_id));
    for (const row of expectedForce) near(means.data.find(p => p.y === row.recipe_name_ru).x, row.value);
    assert.ok(force.yAxis[0].categories.includes('Стандартная'));
    assert.equal(run('recipes_table', params).rows.length, ids.length);
    const sugar = run('sucrose_reduction', params);
    assert.equal(sugar.series.data[0].data.length, rid === 'all' ? 4 : rid === 'control' ? 0 : 1);
    assert.equal(run('candidate_table', params).rows.length, rid === 'all' ? 4 : rid === 'control' ? 0 : 1);
    assert.equal(run('statistics_table', params).rows.length, rid === 'all' ? 4 : rid === 'control' ? 0 : 1);
    for (const metric of [...new Set(data.physical.map(r => r.metric_id))]) {
        const chosen = {recipe_id: [rid], physical_metric_id: [metric]};
        const graph = run('physical_graph', chosen);
        const rows = data.physical.filter(r => r.metric_id === metric && ids.includes(r.recipe_id));
        assert.equal(graph.series.data[0].data.length, ids.length);
        assert.equal(graph.xAxis.title.text, rows[0].unit);
        assert.ok(graph.yAxis[0].categories.includes('Стандартная'));
        for (const point of graph.series.data[0].data) near(point.x, rows.find(r => r.recipe_name_ru === point.y).value);
        const difference = run('physical_delta', chosen);
        assert.equal(difference.xAxis.title.text, rows[0].delta_unit);
        for (const point of difference.series.data[0].data) near(point.x, rows.find(r => r.recipe_name_ru === point.y).value - rows[0].control_mean);
        const tbl = run('physical_table', chosen);
        assert.equal(tbl.rows.length, ids.length);
        for (const row of tbl.rows) near(field(row, tbl, 'control_mean').value, rows[0].control_mean);
        if (metric === 'colour_delta_e76') {
            for (const row of tbl.rows) {
                assert.equal(field(row, tbl, 'sd_between_batches').value, '—');
                assert.equal(field(row, tbl, 'sd_between_batches').type, 'text');
                assert.equal(field(row, tbl, 'relative_display').value, '—');
                assert.equal(field(row, tbl, 'ci_display').value, '—');
            }
        }
    }
    for (const metric of ['key_attributes', ...new Set(data.sensory.map(r => r.metric_id))]) {
        const chosen = {recipe_id: [rid], sensory_metric_id: [metric]};
        const expected = data.sensory.filter(r => ids.includes(r.recipe_id) && (metric === 'key_attributes' ? data.config.key_sensory_metrics.includes(r.metric_id) : r.metric_id === metric));
        const graph = run('sensory_graph', chosen);
        assert.equal(graph.xAxis.max, 100);
        assert.equal(graph.series.data.reduce((n, s) => n + s.data.length, 0), expected.length);
        for (const series of graph.series.data) {
            for (const point of series.data) near(point.x, expected.find(r => r.recipe_name_ru === series.name && r.metric_label_ru === point.y).published_mean);
        }
        const delta = run('sensory_delta', chosen);
        for (const series of delta.series.data) {
            for (const point of series.data) near(point.x, expected.find(r => r.recipe_name_ru === series.name && r.metric_label_ru === point.y).delta_vs_control_points);
        }
        const tbl = run('sensory_table', chosen);
        assert.equal(tbl.rows.length, expected.length);
        for (const row of tbl.rows) {
            const label = field(row, tbl, 'metric_label_ru').value;
            near(field(row, tbl, 'control_published_mean').value, data.sensory.find(r => r.recipe_id === 'control' && r.metric_label_ru === label).published_mean);
            const source = data.sensory.find(r => r.metric_label_ru === label);
            if (source.definition_status !== 'supported_by_S1') assert.ok(field(row, tbl, 'definition_note').value.includes('отсутствует'));
        }
    }
}
assert.throws(() => run('physical_graph', {physical_metric_id: ['not_a_metric']}));
assert.throws(() => run('sensory_graph', {sensory_metric_id: ['not_a_metric']}));
assert.throws(() => run('recipes_table', {recipe_id: ['not_a_recipe']}));
// Негативные регрессии: проверка должна выявлять неверные конверты и скрытые подписи.
assert.throws(() => schemaCheck('markdown', {text: 'Скрытый текст'}));
const brokenTable = run('recipes_table'); brokenTable.rows[0] = {values: [1]};
assert.throws(() => schemaCheck('table', brokenTable));
const hiddenAxis = run('sucrose_amount'); hiddenAxis.yAxis[0].labels.enabled = false;
assert.throws(() => schemaCheck('gravity_charts', hiddenAxis));
const badCategory = run('sucrose_amount'); badCategory.series.data[0].data[0].y = 'not_a_recipe';
assert.throws(() => schemaCheck('gravity_charts', badCategory));
const badLabels = run('force_graph'); badLabels.series.data.find(s => s.name === 'Среднее рецептуры').data[0].label = undefined;
assert.throws(() => schemaCheck('gravity_charts', badLabels));
if (process.argv[4]) fs.writeFileSync(process.argv[4], JSON.stringify(gravityResults));
console.log(JSON.stringify({schemas_checked: schemasChecked, negative_schema_cases: 5, status: 'passed', prepare_executions: executions, recipe_states: 6, physical_metrics: 14, sensory_states: 41}));

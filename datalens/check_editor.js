// Проверка Prepare в Node: настоящий интерфейс DataLens этим не имитируется.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const plan = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const data = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const codes = Object.fromEntries(plan.requests.filter(r => r.key).map(r => [r.key, r.body.entry.data.prepare]));
let executions = 0;
function run(key, params = {}) {
    const sandbox = {module: {exports: {}}, Editor: {getParams: () => params}};
    vm.runInNewContext(codes[key], sandbox, {timeout: 2000});
    executions++;
    return JSON.parse(JSON.stringify(sandbox.module.exports));
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
console.log(JSON.stringify({status: 'passed', prepare_executions: executions, recipe_states: 6, physical_metrics: 14, sensory_states: 41}));

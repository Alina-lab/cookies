// Prepare для официального DataLens Editor. DATA и VIEW добавляет deploy.py.
const params = Editor.getParams();
function parameter(name, fallback) {
    const value = params[name];
    if (value === undefined || value === null || value.length === 0) return fallback;
    return Array.isArray(value) ? String(value[0]) : String(value);
}
const recipe = parameter('recipe_id', 'all');
const physicalMetric = parameter('physical_metric_id', DATA.config.default_physical_metric);
const sensoryMetric = parameter('sensory_metric_id', DATA.config.default_sensory_metric);
const recipeIds = DATA.recipes.map(r => r.recipe_id);
if (recipe !== 'all' && !recipeIds.includes(recipe)) throw new Error('Неизвестная рецептура');
const palette = DATA.config.recipe_colours;
const name = id => DATA.recipes.find(r => r.recipe_id === id).recipe_name_ru;
const colour = id => palette[id];
const selected = (row, keepControl = true) => recipe === 'all' || row.recipe_id === recipe || (keepControl && row.recipe_id === 'control');
const ordered = rows => rows.slice().sort((a, b) => a.recipe_order - b.recipe_order);
function fmt(value, digits = 2) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return Number(value).toFixed(digits).replace('.', ',');
}
function table(columns, rows) {
    return {
        head: columns.map(([id, title, numeric = false]) => ({id, name: title, type: numeric ? 'number' : 'text',
            ...(numeric ? {formatter: {precision: id.startsWith('n_') ? 0 : id === 'p_adjusted' ? 3
                : VIEW === 'physical_table' && physicalMetric === 'water_activity' ? 4 : 2}} : {})})),
        rows: rows.map(row => ({cells: columns.map(([key]) => row[key] === null || row[key] === undefined
            ? {value: '—', type: 'text'} : {value: row[key]})})),
    };
}
function chart(title, unit, categories, series, zeroMinimum = false) {
    return {
        title: {text: title, style: {fontSize: '14px', fontWeight: 'normal'}},
        series: {data: series},
        xAxis: {type: 'linear', title: {text: unit}, grid: {enabled: true}, ...(zeroMinimum ? {min: 0} : {})},
        yAxis: [{type: 'category', categories: categories.slice().reverse(), labels: {style: {fontSize: '11px'}, maxWidth: '45%'}}],
        legend: {enabled: false},
    };
}
function bars(rows, valueField, title, unit, zeroMinimum = false) {
    const data = rows.map(r => ({x: r[valueField], y: r.recipe_name_ru, color: colour(r.recipe_id),
        label: fmt(r[valueField], r.metric_id === 'water_activity' ? 4 : 2), custom: {recipe_id: r.recipe_id, metric_id: r.metric_id || null}}));
    return chart(title, unit, rows.map(r => r.recipe_name_ru), [{type: 'bar-y', name: title, data,
        grouping: false, dataLabels: {enabled: true}}], zeroMinimum);
}
function physicalRows(metric) {
    const rows = ordered(DATA.physical.filter(r => r.metric_id === metric && selected(r)));
    if (!rows.length || new Set(rows.map(r => r.unit)).size !== 1) throw new Error('Нужен один физический показатель с одной единицей');
    return rows;
}
function sensoryRows() {
    const metrics = sensoryMetric === 'key_attributes' ? DATA.config.key_sensory_metrics : [sensoryMetric];
    if (!metrics.every(id => DATA.sensory.some(r => r.metric_id === id))) throw new Error('Неизвестный сенсорный показатель');
    return DATA.sensory.filter(r => metrics.includes(r.metric_id) && selected(r))
        .sort((a, b) => metrics.indexOf(a.metric_id) - metrics.indexOf(b.metric_id) || a.recipe_order - b.recipe_order);
}

let result;
if (VIEW === 'kpi_max') {
    const max = Math.max(...DATA.recipes.map(r => r.sucrose_reduction_pct));
    result = {markdown: `# ${fmt(max)}%\n\nМаксимальное **исследованное** снижение добавленной сахарозы. Не предел допустимой замены.`};
} else if (VIEW === 'kpi_control') {
    const standard = DATA.recipes.find(r => r.recipe_id === 'control');
    result = {markdown: `# ${fmt(standard.sucrose_g_per_100g_flour)} г\n\nСахароза стандартной рецептуры **на 100 г муки**.`};
} else if (VIEW === 'kpi_variants') {
    result = {markdown: `# ${DATA.recipes.filter(r => r.recipe_id !== 'control').length} варианта\n\nПлюс стандартная рецептура. Тип и дозировка заменителя различаются.`};
} else if (VIEW === 'recipes_table') {
    const rows = ordered(DATA.recipes.filter(r => selected(r)));
    result = table([
        ['recipe_name_ru', 'Рецептура'], ['sucrose_g_per_100g_flour', 'Сахароза, г / 100 г муки', true],
        ['sucrose_removed_g_per_100g_flour', 'Удалено сахарозы, г', true], ['sucrose_reduction_pct', 'Снижение, %', true],
        ['fibre_ingredient_g_per_100g_flour', 'Новый ингредиент, г', true], ['total_recipe_g_per_100g_flour', 'Сумма рецептуры, г', true],
    ], rows);
} else if (VIEW === 'sucrose_amount') {
    result = bars(ordered(DATA.recipes.filter(r => selected(r))), 'sucrose_g_per_100g_flour',
        'Добавленная сахароза по рецептуре', 'г на 100 г муки', true);
} else if (VIEW === 'sucrose_reduction') {
    const rows = ordered(DATA.recipes.filter(r => selected(r, false) && r.recipe_id !== 'control'));
    result = bars(rows, 'sucrose_reduction_pct', 'Снижение относительно 29,45 г сахарозы', '%', true);
} else if (VIEW === 'force_graph') {
    const rows = physicalRows('breaking_strength_n');
    const ids = rows.map(r => r.recipe_id);
    const batches = DATA.batches.filter(r => r.metric_id === 'breaking_strength_n' && ids.includes(r.recipe_id));
    const intervals = rows.filter(r => r.mean_ci_low !== null && r.mean_ci_high !== null).map(r => ({
        type: 'line', name: `${r.recipe_name_ru} · отдельный 95% t-интервал`, color: colour(r.recipe_id),
        lineWidth: 2, marker: {enabled: false},
        data: [{x: r.mean_ci_low, y: r.recipe_name_ru}, {x: r.mean_ci_high, y: r.recipe_name_ru}],
    }));
    const series = intervals.concat([
        {type: 'scatter', name: 'Три партийных средних на рецептуру',
            data: batches.map(r => ({x: r.batch_mean, y: r.recipe_name_ru, radius: 3, color: colour(r.recipe_id),
                custom: {batch_id: r.batch_id}}))},
        {type: 'scatter', name: 'Среднее рецептуры', symbolType: 'diamond',
            data: rows.map(r => ({x: r.value, y: r.recipe_name_ru, radius: 6, color: colour(r.recipe_id)}))},
    ]);
    result = chart('Сила разрушения: партии, средние и условные 95% t-интервалы', 'Н', rows.map(r => r.recipe_name_ru), series, true);
} else if (VIEW === 'force_table' || VIEW === 'physical_table') {
    const rows = physicalRows(VIEW === 'force_table' ? 'breaking_strength_n' : physicalMetric).map(r => ({...r,
        relative_display: fmt(r.relative_delta_pct) + (r.relative_delta_pct === null ? '' : '%'),
        n_display: r.n_independent_batches === null ? `${fmt(r.n_batches_used, 0)} для центра; отдельного n ΔE нет` : String(r.n_independent_batches),
        ci_display: r.mean_ci_low === null ? '—' : `${fmt(r.mean_ci_low)}…${fmt(r.mean_ci_high)}`,
        notes: [r.definition_ru, r.qc_note_ru || ''].filter(Boolean).join(' '),
    }));
    result = table([
        ['recipe_name_ru', 'Рецептура'], ['value', 'Среднее / расстояние', true], ['unit', 'Единица'],
        ['control_mean', 'Стандарт', true], ['delta_vs_control', 'Разность', true], ['delta_unit', 'Ед. разности'],
        ['relative_display', 'Изменение, %'], ['sd_between_batches', 'SD партий', true], ['n_display', 'Независимых партий'],
        ['ci_display', 'Отдельный 95% t-интервал'], ['notes', 'Пояснение и флаги источника'],
    ], rows);
} else if (VIEW === 'batches_table') {
    const rows = ordered(DATA.batches.filter(r => r.metric_id === 'breaking_strength_n' && selected(r)));
    result = table([
        ['recipe_name_ru', 'Рецептура'], ['batch_id', 'Условная партия'], ['batch_mean', 'Средняя сила, Н', true],
        ['n_values_observed', 'Измерено изделий', true], ['n_values_missing', 'Пропущено измерений', true],
    ], rows);
} else if (VIEW === 'statistics_table') {
    result = table([
        ['recipe_name_ru', 'Вариант'], ['difference_vs_control', 'Разность, Н', true], ['p_adjusted', 'p с Holm', true],
        ['ci_low', 'Нижняя граница, Н', true], ['ci_high', 'Верхняя граница, Н', true],
        ['n_variant_batches', 'Партий варианта', true], ['n_control_batches', 'Партий стандарта', true],
        ['method_ru', 'Метод'], ['ci_label', 'Тип интервала'], ['assumptions_ru', 'Допущения'], ['limitations_ru', 'Ограничения'],
    ], DATA.statistics.filter(r => selected(r, false)).map(r => ({...r,
        ci_label: 'Отдельные 98,75% Welch–Bonferroni; номинальные семейные 95%, четыре разности; не интервалы Holm'})));
} else if (VIEW === 'physical_graph' || VIEW === 'physical_delta') {
    const rows = physicalRows(physicalMetric);
    const metric = rows[0];
    result = VIEW === 'physical_graph'
        ? bars(rows, 'value', metric.metric_name_ru, metric.unit)
        : bars(rows.filter(r => r.recipe_id !== 'control'), 'delta_vs_control', `${metric.metric_name_ru}: разность со стандартом`, metric.delta_unit);
} else if (VIEW === 'sensory_graph' || VIEW === 'sensory_delta') {
    const rows = sensoryRows();
    const attributes = [...new Set(rows.map(r => r.metric_label_ru))];
    const differences = VIEW === 'sensory_delta';
    const groups = ordered(DATA.recipes).filter(r => selected(r) && (!differences || r.recipe_id !== 'control'));
    const series = groups.map(g => ({type: 'scatter', name: g.recipe_name_ru, color: colour(g.recipe_id),
        data: rows.filter(r => r.recipe_id === g.recipe_id).map(r => ({
            x: differences ? r.delta_vs_control_points : r.published_mean, y: r.metric_label_ru, radius: 4,
            custom: {metric_id: r.metric_id, data_status: 'published_summary_only'},
        })),
    }));
    result = chart(differences ? 'Разности опубликованных сенсорных средних' : 'Опубликованные сенсорные средние',
        differences ? 'пункты шкалы 0–100 относительно стандарта' : 'интенсивность по шкале 0–100', attributes, series);
    result.legend = {enabled: true};
    if (!differences) {result.xAxis.min = 0; result.xAxis.max = 100;}
} else if (VIEW === 'sensory_table') {
    result = table([
        ['metric_label_ru', 'Сенсорный признак'], ['recipe_name_ru', 'Рецептура'],
        ['published_mean', 'Опубликованное среднее', true], ['control_published_mean', 'Среднее стандарта', true],
        ['delta_vs_control_points', 'Разность, пункты', true], ['status_display', 'Статус данных'], ['definition_note', 'Ограничение определения'],
    ], sensoryRows().map(r => ({...r, status_display: 'Опубликованные средние; индивидуальных оценок нет',
        definition_note: r.definition_status === 'supported_by_S1' ? '' : 'Определение этого признака в S1 отсутствует; границы шкалы не восстановлены.'})));
} else if (VIEW === 'candidate_table') {
    result = table([
        ['recipe_name_ru', 'Рецептура'], ['sucrose_reduction_pct', 'Снижение сахарозы, %', true],
        ['mean_breaking_strength_n', 'Средняя сила, Н', true], ['breaking_relative_delta_pct', 'Изменение силы, %', true],
        ['bitterness_delta_points', 'Горечь, разность п.', true], ['burnt_taste_delta_points', 'Подгорелый вкус, разность п.', true],
        ['colour_delta_e76', 'ΔE*76', true], ['n_independent_batches', 'Партий', true],
    ], ordered(DATA.candidates.filter(r => selected(r, false))));
} else if (VIEW === 'candidate_details') {
    const rows = ordered(DATA.candidates.filter(r => selected(r, false)));
    const text = rows.map(r => `### ${r.recipe_name_ru}\n\n`
        + `**Основание для испытаний:** ${DATA.config.advantages_ru[r.recipe_id]}\n\n`
        + `${r.physical_changes_ru} ${r.sensory_changes_ru}\n\n`
        + `**Рекомендация:** ${r.recommendation_ru}\n\n**Ограничения:** ${r.limitations_ru} ${r.confidence_ru}`).join('\n\n---\n\n');
    result = {markdown: text || 'Стандарт служит основой сравнения. Выберите экспериментальную рецептуру или все варианты.'};
} else {
    throw new Error('Неизвестный чарт');
}
module.exports = result;

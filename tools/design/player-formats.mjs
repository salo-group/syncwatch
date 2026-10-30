// First-party polygon geometry and presentation contract; no platform icon/font dependency.
const q = value => JSON.stringify(value);
export function validatePlayer(s, components, copy) {
    const c = components.components.PlayerChrome;
    if (s.schemaVersion !== 1 || q(s.states) !== q(c.states) || q(s.actions) !== q(c.renderedActions)
        || new Set(s.actions).size !== s.actions.length || s.actions.some(a => !c.actions.includes(a))
        || s.maximumPositionMs !== 604800000 || !s.icons || !Object.keys(s.icons).length) throw Error('Player contract differs');
    if (!Array.isArray(s.completedNavigationActions) || !s.completedNavigationActions.length
        || new Set(s.completedNavigationActions).size !== s.completedNavigationActions.length
        || s.completedNavigationActions.some(a => !['previousFile', 'nextFile'].includes(a) || !s.actions.includes(a))) {
        throw Error('Invalid completed player navigation');
    }
    const input = s.input;
    if (!input || !Number.isFinite(input.volumeStep) || input.volumeStep <= 0 || input.volumeStep > 1
        || !Array.isArray(input.keys) || !input.keys.length || input.keys.length > 32
        || new Set(input.keys.map(b => b.key)).size !== input.keys.length
        || input.keys.some(b => !['Space', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'KeyM', 'KeyF', 'Escape'].includes(b.key)
            || !['playPause', 'skip', 'setMovieVolume', 'muteMovie', 'toggleFullscreen'].includes(b.action)
            || !s.actions.includes(b.action) || typeof b.repeat !== 'boolean'
            || b.fullscreenOnly !== undefined && typeof b.fullscreenOnly !== 'boolean'
            || (['skip', 'setMovieVolume'].includes(b.action) ? ![-1, 1].includes(b.value) : b.value !== undefined)
            || Object.keys(b).some(k => !['key', 'action', 'value', 'repeat', 'fullscreenOnly'].includes(k)))) {
        throw Error('Invalid player input contract');
    }
    for (const state of s.states) if (!Object.hasOwn(copy.locales.en, state)) throw Error('Missing player state copy');
    for (const [name, paths] of Object.entries(s.icons)) {
        if (!/^[a-z]+$/.test(name) || !Array.isArray(paths) || !paths.length || paths.length > 8
            || paths.some(p => !Array.isArray(p) || p.length < 6 || p.length > 64 || p.length % 2
                || p.some(v => !Number.isFinite(v) || v < 0 || v > 20))) throw Error('Invalid player polygon');
    }
    for (const icon of ['play','pause','backward','forward','previous','next','volume','muted','library','call','fullscreen','end','settings','playlist','subtitles','audio']) {
        if (!s.icons[icon]) throw Error('Missing player icon');
    }
}
export function swiftPlayer(s) {
    return '\npublic struct PlayerInputBinding: Sendable {\n    public let key: String\n    public let action: String\n    public let value: Double?\n    public let repeats: Bool\n    public let fullscreenOnly: Bool\n}\n\npublic enum SyncWatchPlayer {\n' +
        '    public static let states = [\n' + s.states.map(v => `        ${q(v)},\n`).join('') + '    ]\n' +
        '    public static let actions = [\n' + s.actions.map(v => `        ${q(v)},\n`).join('') + '    ]\n' +
        `    public static let completedNavigationActions: Set<String> = [${s.completedNavigationActions.map(q).join(', ')}]\n` +
        `    public static let inputVolumeStep = ${s.input.volumeStep}\n` +
        '    public static let inputBindings: [PlayerInputBinding] = [\n' + s.input.keys.map(b =>
            `        PlayerInputBinding(key: ${q(b.key)}, action: ${q(b.action)}, value: ${b.value ?? 'nil'}, repeats: ${b.repeat}, fullscreenOnly: ${b.fullscreenOnly ?? false}),\n`).join('') + '    ]\n' +
        '    public static let maximumPositionMs: Int64 = 604_800_000\n' +
        '    public static let icons: [String: [[Double]]] = [\n' + Object.entries(s.icons).map(([k,v]) =>
            `        ${q(k)}: [\n` + v.map(p => '            [\n' + p.map(n => `                ${n},\n`).join('') + (v.length === 1 ? '            ]\n' : '            ],\n')).join('') + '        ],\n').join('') + '    ]\n}\n';
}
export function csharpPlayer(s) {
    return '\npublic sealed record PlayerInputBinding(string Key, string Action, double? Value, bool Repeats, bool FullscreenOnly);\n\npublic static class SyncWatchPlayer\n{\n' +
        `    public static string[] States => [${s.states.map(q).join(', ')}];\n` +
        `    public static string[] Actions => [${s.actions.map(q).join(', ')}];\n` +
        `    public static string[] CompletedNavigationActions => [${s.completedNavigationActions.map(q).join(', ')}];\n` +
        `    public const double InputVolumeStep = ${s.input.volumeStep};\n` +
        '    public static PlayerInputBinding[] InputBindings => [\n' + s.input.keys.map(b =>
            `        new(${q(b.key)}, ${q(b.action)}, ${b.value ?? 'null'}, ${b.repeat}, ${b.fullscreenOnly ?? false}),\n`).join('') + '    ];\n' +
        `    public const long MaximumPositionMs = ${s.maximumPositionMs};\n` +
        '    public static System.Collections.Generic.IReadOnlyDictionary<string, double[][]> Icons => new System.Collections.Generic.Dictionary<string, double[][]>\n    {\n' +
        Object.entries(s.icons).map(([k,v]) => `        [${q(k)}] = ${q(v)},\n`).join('') + '    };\n}\n';
}
export function kotlinPlayer(s) {
    return '\ndata class PlayerInputBinding(val key: String, val action: String, val value: Double?, val repeats: Boolean, val fullscreenOnly: Boolean)\n\nobject SyncWatchPlayer {\n' +
        `    val states = listOf(${s.states.map(q).join(', ')})\n` +
        `    val actions = setOf(${s.actions.map(q).join(', ')})\n` +
        `    val completedNavigationActions = setOf(${s.completedNavigationActions.map(q).join(', ')})\n` +
        `    const val inputVolumeStep = ${s.input.volumeStep}\n` +
        '    val inputBindings = listOf(\n' + s.input.keys.map(b =>
            `        PlayerInputBinding(${q(b.key)}, ${q(b.action)}, ${b.value === undefined ? 'null' : b.value.toFixed(1)}, ${b.repeat}, ${b.fullscreenOnly ?? false}),\n`).join('') + '    )\n' +
        `    const val maximumPositionMs: Long = ${s.maximumPositionMs}L\n` +
        '    val icons = mapOf(\n' + Object.entries(s.icons).map(([k,v]) => `        ${q(k)} to listOf(${v.map(p => 'listOf(' + p.map(n => n + (Number.isInteger(n) ? '.0f' : 'f')).join(', ') + ')').join(', ')}),\n`).join('') + '    )\n}\n';
}

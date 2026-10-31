// First-party SettingsDialog schema and typed native metadata.
const q = value => JSON.stringify(value);
const kq = value => q(value).replace(/\$/g, '\\$');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export function validateSettings(spec, components, copy) {
    const contract = components.components.SettingsDialog;
    if (spec.schemaVersion !== 1 || !same(spec.sections, contract.sections) || !same(spec.states, contract.states)
        || !Array.isArray(spec.fields) || !spec.fields.length) throw Error('Settings contract differs');
    const ids = new Set();
    for (const field of spec.fields) {
        if (!/^[a-z][a-zA-Z0-9]*$/.test(field.id) || ids.has(field.id) || !spec.sections.includes(field.section)
            || !['text', 'secret', 'choice', 'boolean', 'folder'].includes(field.kind) || !Object.hasOwn(copy.locales.en, field.label)
            || !Number.isInteger(field.maxLength) || field.maxLength < 1 || field.maxLength > 512
            || !Array.isArray(field.options) || new Set(field.options).size !== field.options.length
            || (field.kind === 'choice') !== (field.options.length > 0)) throw Error('Invalid settings field');
        ids.add(field.id);
        for (const option of field.options) {
            if (!/^[a-z0-9]+$/.test(option) || !Object.hasOwn(copy.locales.en, 'option' + option[0].toUpperCase() + option.slice(1))) throw Error('Missing settings option copy');
        }
        const value = spec.defaults[field.id];
        if (typeof value !== 'string' || value.length > field.maxLength || /[\x00-\x1f\x7f-\x9f]/.test(value)
            || (field.kind === 'choice' && !field.options.includes(value))
            || (field.kind === 'boolean' && !['true', 'false'].includes(value))) throw Error('Invalid settings default');
    }
    if (Object.keys(spec.defaults).length !== ids.size || Object.keys(spec.defaults).some(id => !ids.has(id))) throw Error('Settings defaults differ');
    for (const section of spec.sections) if (!spec.fields.some(f => f.section === section)
        || !Object.hasOwn(copy.locales.en, section) || !Object.hasOwn(copy.locales.en, section + 'Hint')) throw Error('Incomplete settings section');
}
export function swiftSettings(s) {
    return '\npublic struct SyncWatchSettingsField: Sendable {\n    public let id: String\n    public let section: String\n    public let kind: String\n    public let label: String\n    public let options: [String]\n    public let maxLength: Int\n}\n\npublic enum SyncWatchSettings {\n' +
        `    public static let sections = [${s.sections.map(q).join(', ')}]\n` +
        `    public static let states = [${s.states.map(q).join(', ')}]\n` +
        '    public static let defaults: [String: String] = [\n' + Object.entries(s.defaults).map(([k,v]) => `        ${q(k)}: ${q(v)},\n`).join('') + '    ]\n' +
        '    public static let fields: [SyncWatchSettingsField] = [\n' + s.fields.map(f => `        SyncWatchSettingsField(\n            id: ${q(f.id)}, section: ${q(f.section)}, kind: ${q(f.kind)}, label: ${q(f.label)},\n            options: [${f.options.map(q).join(', ')}], maxLength: ${f.maxLength}),\n`).join('') + '    ]\n}\n';
}
export function csharpSettings(s) {
    return '\npublic sealed record SyncWatchSettingsField(string Id, string Section, string Kind, string Label, string[] Options, int MaxLength);\n\npublic static class SyncWatchSettings\n{\n' +
        `    public static string[] Sections => [${s.sections.map(q).join(', ')}];\n` +
        `    public static string[] States => [${s.states.map(q).join(', ')}];\n` +
        '    public static System.Collections.Generic.IReadOnlyDictionary<string, string> Defaults => new System.Collections.Generic.Dictionary<string, string>\n    {\n' + Object.entries(s.defaults).map(([k,v]) => `        [${q(k)}] = ${q(v)},\n`).join('') + '    };\n' +
        '    public static SyncWatchSettingsField[] Fields =>\n    [\n' + s.fields.map(f => `        new(${q(f.id)}, ${q(f.section)}, ${q(f.kind)}, ${q(f.label)}, [${f.options.map(q).join(', ')}], ${f.maxLength}),\n`).join('') + '    ];\n}\n';
}
export function kotlinSettings(s) {
    return '\ndata class SyncWatchSettingsField(val id: String, val section: String, val kind: String, val label: String, val options: List<String>, val maxLength: Int)\n\nobject SyncWatchSettings {\n' +
        `    val sections = listOf(${s.sections.map(kq).join(', ')})\n` +
        `    val states = listOf(${s.states.map(kq).join(', ')})\n` +
        '    val defaults = mapOf(\n' + Object.entries(s.defaults).map(([k,v]) => `        ${kq(k)} to ${kq(v)},\n`).join('') + '    )\n' +
        '    val fields = listOf(\n' + s.fields.map(f => `        SyncWatchSettingsField(${kq(f.id)}, ${kq(f.section)}, ${kq(f.kind)}, ${kq(f.label)}, listOf(${f.options.map(kq).join(', ')}), ${f.maxLength}),\n`).join('') + '    )\n}\n';
}

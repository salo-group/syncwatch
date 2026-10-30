import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {after, test} from 'node:test';
import {validSelection, selectionRequest, validateSelectionMenu} from '../selection-menu-formats.mjs';
import {validPlayerSetting} from '../player-settings-formats.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const platform = {'darwin': 'macos-arm64', 'linux': 'linux-x64', 'win32': 'windows-x64'}[process.platform];
const runtime = process.env.SYNCWATCH_DESIGN_RUNTIME ?? join(root, 'artifacts/dependencies/design-generator', platform, 'payload/runtime');
const temporary = mkdtempSync(join(tmpdir(), 'syncwatch-native-design-'));
after(() => rmSync(temporary, {recursive: true, force: true}));

test('library companion labels remain declared and validated by the common design', () => {
    const policy = JSON.parse(readFileSync(join(root, 'packages/design/selection-menu.json')));
    const components = JSON.parse(readFileSync(join(root, 'packages/design/components.json')));
    const copy = JSON.parse(readFileSync(join(root, 'packages/design/copy.json')));
    validateSelectionMenu(policy, components, copy);
    for (const field of ['normal', 'duplicate']) {
        const changed = structuredClone(policy);
        changed.externalFile.libraryLabels[field] = 'platformDefault';
        assert.throws(() => validateSelectionMenu(changed, components, copy), /library companion labels/);
    }
    const missing = structuredClone(policy);
    delete missing.externalFile.libraryLabels;
    assert.throws(() => validateSelectionMenu(missing, components, copy), /library companion labels/);
});
const files = ['macos/SyncWatchTokens.swift', 'windows/SyncWatchTokens.g.cs', 'windows/SyncWatchTheme.g.xaml', 'design-manifest.json', 'linux/SyncWatchTokens.kt'];
function generate(design, output, check = false) {
    const result = spawnSync(process.execPath, [join(root, 'tools/design/generate.mjs'), '--runtime', runtime,
        '--design', design, '--output', output, ...(check ? ['--check'] : [])], {
        cwd: temporary, encoding: 'utf8', timeout: 30000,
        env: {...process.env, NODE_PATH: '', HTTP_PROXY: 'http://127.0.0.1:9', HTTPS_PROXY: 'http://127.0.0.1:9', npm_config_offline: 'true'},
    });
    assert.equal(result.error, undefined);
    return result;
}
const success = result => assert.equal(result.status, 0, result.stderr + result.stdout);
let sequence = 0;
function fixture(change = () => {}) {
    const directory = join(temporary, 'design-' + sequence++);
    cpSync(join(root, 'packages/design'), directory, {recursive: true, filter: source => !source.includes('/.build') && !source.includes('\\.build')});
    const geometry = JSON.parse(readFileSync(join(directory, 'tokens/geometry.json')));
    const palette = JSON.parse(readFileSync(join(directory, 'tokens/palette.json')));
    change(geometry, palette);
    writeFileSync(join(directory, 'tokens/geometry.json'), JSON.stringify(geometry));
    writeFileSync(join(directory, 'tokens/palette.json'), JSON.stringify(palette));
    return directory;
}
const name = id => id.split('.').map(part => part[0].toUpperCase() + part.slice(1)).join('');

test('every player selection kind has a reachable common popover anchor', () => {
    const layout = JSON.parse(readFileSync(join(root, 'packages/design/layouts.json')));
    const policy = JSON.parse(readFileSync(join(root, 'packages/design/selection-menu.json')));
    const player = JSON.parse(readFileSync(join(root, 'packages/design/player.json')));
    const component = JSON.parse(readFileSync(join(root, 'packages/design/components.json'))).components.PlayerChrome;
    const anchors = [];
    function visit(node) {
        if (node.props?.popoverSlot) anchors.push(node.props);
        for (const child of node.children ?? []) visit(child);
    }
    visit(layout.components.Player);
    for (const kind of policy.kinds) {
        const anchor = anchors.find(props => props.popoverSlot === kind.id + 'Selection');
        assert.ok(anchor, kind.id + ' menu has no player anchor');
        assert.ok(player.actions.includes(anchor.action), kind.id + ' open action cannot be dispatched');
        assert.ok(component.renderedActions.includes(anchor.action), kind.id + ' action is not integrated');
        assert.equal(anchor.popoverEdge, 'top');
        assert.equal(anchor.popoverDismiss, 'dismissSelection');
    }
});

test('player settings numeric validation agrees with the cross-client input corpus', () => {
    const spec = JSON.parse(readFileSync(join(root, 'packages/design/player-settings.json')));
    const corpus = JSON.parse(readFileSync(join(root, 'packages/design/tests/fixtures/player-settings.json')));
    assert.deepEqual(corpus.fields.map(f => f.id).sort(), Object.keys(spec.defaults).sort());
    for (const fixture of corpus.fields) {
        const field = spec.fields.find(f => f.id === fixture.id);
        for (const value of fixture.valid) assert.equal(validPlayerSetting(field, value), true, field.id + ': ' + value);
        for (const value of fixture.invalid) assert.equal(validPlayerSetting(field, value), false, field.id + ': ' + value);
    }
});

test('player settings defaults, units, colors and fractional bounds reach every client', () => {
    const spec = JSON.parse(readFileSync(join(root, 'packages/design/player-settings.json')));
    const output = join(temporary, 'player-settings'); success(generate(join(root, 'packages/design'), output));
    assert.deepEqual(JSON.parse(readFileSync(join(output, files[3]))).playerSettings, spec);
    for (const file of [files[0], files[1], files[4]]) {
        const source = readFileSync(join(output, file), 'utf8');
        assert.ok(source.includes('SyncWatchPlayerSettings'));
        for (const field of spec.fields) {
            for (const value of [field.id, field.section, field.kind, field.label, field.unit, spec.defaults[field.id], ...field.options.flatMap(o => [o.id, o.label, ...(o.colorToken ? [o.colorToken] : [])])]) {
                assert.ok(source.includes(JSON.stringify(value)), file + ': ' + value);
            }
        }
        assert.ok(source.includes('0.05') && !source.includes('0.05.0'));
    }
});

test('player settings reject unsafe bounds, steps, defaults, swatches and layout drift', () => {
    for (const mutate of [s => s.sections.reverse(), s => s.states.pop(), s => s.defaults.brightness = '1e0',
        s => s.defaults.extra = '0', s => s.fields[3].step = 0, s => s.fields[3].maximum = 18,
        s => s.fields[3].minimum = null, s => s.fields[3].step = 0.333,
        s => s.fields[4].options[0].colorToken = 'missing', s => s.fields[4].options[0].label = 'missing',
        s => s.fields[6].row = 0, s => s.fields[0].options.push(s.fields[0].options[0]),
        s => s.fields[0].label = 'missing', s => s.fields[0].unit = 'missing', s => s.fields[0].kind = 'text']) {
        const design = fixture(); const path = join(design, 'player-settings.json'); const spec = JSON.parse(readFileSync(path));
        mutate(spec); writeFileSync(path, JSON.stringify(spec));
        assert.notEqual(generate(design, join(temporary, 'bad-player-settings')).status, 0);
    }
});

test('player states, supported actions and first-party polygons generate into all clients', () => {
    const output = join(temporary, 'player-metadata');
    success(generate(join(root, 'packages/design'), output));
    const manifest = JSON.parse(readFileSync(join(output, files[3])));
    assert.deepEqual(manifest.player, JSON.parse(readFileSync(join(root, 'packages/design/player.json'))));
    for (const file of [files[0], files[1], files[4]]) {
        const text = readFileSync(join(output, file), 'utf8');
        assert.ok(text.includes('SyncWatchPlayer'));
        for (const binding of manifest.player.input.keys) assert.ok(text.includes('"' + binding.key + '"'));
        for (const state of manifest.player.states) assert.ok(text.includes('"' + state + '"'));
        for (const [icon, polygons] of Object.entries(manifest.player.icons)) {
            assert.ok(text.includes('"' + icon + '"'));
            assert.ok(polygons.every(p => p.length >= 6 && p.length % 2 === 0));
        }
        assert.ok(text.replaceAll('_', '').includes('604800000'));
    }
});

test('invalid player contracts and polygon bounds fail before native generation', () => {
    for (const change of [s => s.schemaVersion = 2, s => s.states.reverse(), s => s.actions.push('unknown'),
        s => delete s.completedNavigationActions, s => s.completedNavigationActions = [],
        s => s.completedNavigationActions = ['playPause'], s => s.completedNavigationActions = ['unknown'],
        s => s.completedNavigationActions = ['nextFile', 'nextFile'],
        s => delete s.input, s => s.input.volumeStep = 0, s => s.input.volumeStep = 2,
        s => s.input.keys[0].action = 'endWatching', s => s.input.keys[0].repeat = 'yes',
        s => s.input.keys.push(s.input.keys[0]), s => s.input.keys[1].value = 3,
        s => s.input.keys[0].value = 1, s => s.input.keys[0].fullscreenOnly = 1,
        s => s.maximumPositionMs++, s => s.icons.play = [[0, 1, 2]], s => s.icons.play[0][0] = 21,
        s => s.icons.play[0][0] = null, s => delete s.icons.pause]) {
        const design = fixture();
        const file = join(design, 'player.json');
        const spec = JSON.parse(readFileSync(file)); change(spec); writeFileSync(file, JSON.stringify(spec));
        assert.notEqual(generate(design, join(temporary, 'bad-player')).status, 0);
    }
});

test('fractional player geometry keeps valid native number syntax', () => {
    const design = fixture();
    const file = join(design, 'player.json');
    const spec = JSON.parse(readFileSync(file)); spec.icons.play[0][0] = 6.5;
    writeFileSync(file, JSON.stringify(spec));
    const output = join(temporary, 'fractional-player'); success(generate(design, output));
    assert.ok(readFileSync(join(output, files[0]), 'utf8').includes('6.5,'));
    assert.ok(readFileSync(join(output, files[1]), 'utf8').includes('[[6.5,'));
    const kotlin = readFileSync(join(output, files[4]), 'utf8');
    assert.ok(kotlin.includes('listOf(6.5f,'));
    assert.ok(!kotlin.includes('6.5.0f'));
});
function verifyAllOutputs(directory) {
    const manifest = JSON.parse(readFileSync(join(directory, files[3])));
    const swift = readFileSync(join(directory, files[0]), 'utf8');
    const cs = readFileSync(join(directory, files[1]), 'utf8');
    const xaml = readFileSync(join(directory, files[2]), 'utf8');
    const kotlin = readFileSync(join(directory, files[4]), 'utf8');
    for (const {id, type, value} of manifest.tokens) {
        const identifier = name(id);
        const swiftId = identifier[0].toLowerCase() + identifier.slice(1);
        if (type === 'color') {
            const [r, g, b, a] = value.slice(1).match(/../g).map(byte => parseInt(byte, 16));
            assert.ok(swift.includes(`let ${swiftId} = Color(\n        .sRGB, red: ${r}.0 / 255, green: ${g}.0 / 255, blue: ${b}.0 / 255, opacity: ${a}.0 / 255)`), id);
            assert.ok(kotlin.includes(`const val ${swiftId}: Long = 0x${value.slice(1)}L`), id);
            assert.ok(cs.includes(`Color ${identifier} => Color.FromArgb(${a}, ${r}, ${g}, ${b});`), id);
            assert.ok(xaml.includes(`<Color x:Key="${id}">#${value.slice(7)}${value.slice(1, 7)}</Color>`), id);
            assert.ok(xaml.includes(`<SolidColorBrush x:Key="${id}.brush" Color="{StaticResource ${id}}" />`), id);
        } else {
            assert.ok(swift.includes(`let ${swiftId}: CGFloat = ${value}\n`), id);
            assert.ok(kotlin.includes(`const val ${swiftId}: Float = ${Number.isInteger(value) ? value + '.0' : value}f`), id);
            assert.ok(cs.includes(`const double ${identifier} = ${value};`), id);
            assert.ok(xaml.includes(`<sys:Double x:Key="${id}">${value}</sys:Double>`), id);
            assert.ok(xaml.includes(`<Thickness x:Key="${id}.thickness">${value}</Thickness>`), id);
            assert.ok(xaml.includes(`<CornerRadius x:Key="${id}.cornerRadius">${value}</CornerRadius>`), id);
        }
    }
    assert.equal(new Set(manifest.tokens.map(t => t.id)).size, manifest.tokens.length);
    assert.ok(!swift.includes('UIKit'));
    return manifest;
}

test('all shared token IDs and values agree in Swift, typed C#, XAML, Kotlin and manifest', () => {
    const output = join(temporary, 'actual');
    success(generate(join(root, 'packages/design'), output));
    const manifest = verifyAllOutputs(output);
    assert.ok(manifest.tokens.length >= 40);
    assert.equal(manifest.schemaVersion, 1);
    assert.equal(manifest.generator.styleDictionary, '5.7.0');
    assert.equal(manifest.units, 'logical');
    assert.deepEqual(manifest.tokens.map(t => t.id), manifest.tokens.map(t => t.id).sort());
    const expected = {};
    function visit(group, prefix = []) {
        if (Object.hasOwn(group, 'value')) expected[prefix.join('.')] = group.value;
        else for (const [key, value] of Object.entries(group)) visit(value, [...prefix, key]);
    }
    for (const file of ['geometry', 'palette']) visit(JSON.parse(readFileSync(join(root, `packages/design/tokens/${file}.json`))));
    assert.equal(manifest.tokens.length, Object.keys(expected).length);
    for (const token of manifest.tokens) assert.equal(token.value, token.type === 'color' ? expected[token.id].toUpperCase() + (expected[token.id].length === 7 ? 'FF' : '') : expected[token.id]);
});

test('generation is byte-identical across destinations, repetitions and check mode', () => {
    const first = join(temporary, 'first');
    const second = join(temporary, 'second');
    const design = join(root, 'packages/design');
    success(generate(design, first)); success(generate(design, second)); success(generate(design, first, true));
    for (const file of files) assert.deepEqual(readFileSync(join(first, file)), readFileSync(join(second, file)));
    const manifest = JSON.parse(readFileSync(join(first, files[3])));
    const hash = data => createHash('sha256').update(data).digest('hex');
    for (const [file, digest] of Object.entries(manifest.sources)) assert.equal(hash(readFileSync(join(design, file))), digest);
    assert.equal(hash(JSON.stringify(manifest.sources)), manifest.sourceDigest);
    assert.equal(hash(JSON.stringify({files: manifest.generator.files, sourceRevisions: manifest.generator.sourceRevisions})), manifest.generatorRevision);
    assert.ok(!JSON.stringify(manifest).includes(temporary));
});

test('sRGB trailing alpha, logical dimensions, rounding and transitive aliases survive all formats', () => {
    const design = fixture((g, p) => {
        p.color.shared.translucent = {type: 'color', value: '#12345678'};
        p.color.shared.alias = {type: 'color', value: '{color.shared.translucent}'};
        g.layout.fractional = {type: 'number', value: 1.2345678};
        g.layout.alias = {type: 'number', value: '{layout.fractional}'};
        g.layout.secondAlias = {type: 'number', value: '{layout.alias}'};
        g.layout.zero = {type: 'number', value: 0.0000001};
    });
    const output = join(temporary, 'edge'); success(generate(design, output));
    const tokens = verifyAllOutputs(output).tokens;
    assert.equal(tokens.find(t => t.id === 'color.shared.alias').value, '#12345678');
    assert.equal(tokens.find(t => t.id === 'layout.secondAlias').value, 1.234568);
    assert.equal(tokens.find(t => t.id === 'layout.zero').value, 0);
});

test('unknown types, malformed colors and physical-unit strings fail before output', () => {
    for (const token of [{type: 'font', value: 'Arial'}, {type: 'color', value: '#1234'},
        {type: 'color', value: 'red'}, {type: 'number', value: '18px'}, {type: 'number', value: -1},
        {type: 'number', value: 1000001}, {type: 'number', value: null}]) {
        const design = fixture(g => { g.layout.invalid = token; });
        assert.notEqual(generate(design, join(temporary, 'invalid')).status, 0, JSON.stringify(token));
    }
});

test('missing, cross-type and cyclic aliases fail through validation or upstream resolution', () => {
    for (const reference of ['{missing}', '{color.shared.accent}', '{layout.bad}']) {
        const design = fixture(g => { g.layout.bad = {type: 'number', value: reference}; });
        assert.notEqual(generate(design, join(temporary, 'bad-alias')).status, 0, reference);
    }
    const cycle = fixture(g => {
        g.layout.one = {type: 'number', value: '{layout.two}'};
        g.layout.two = {type: 'number', value: '{layout.one}'};
    });
    assert.notEqual(generate(cycle, join(temporary, 'cycle')).status, 0);
});

test('native name collisions and unsafe identifiers are rejected', () => {
    for (const change of [g => { g.a = {bc: {type: 'number', value: 1}}; g.aBc = {type: 'number', value: 2}; },
        g => { g.class = {type: 'number', value: 1}; }, g => { g.when = {type: 'number', value: 1}; },
        g => { g.layout['bad-name'] = {type: 'number', value: 1}; }]) {
        const design = fixture(change);
        assert.notEqual(generate(design, join(temporary, 'collision')).status, 0);
    }
});

test('theme references must exist, have color types and have matching roles', () => {
    for (const mutation of [t => { t.themes.dark.text = 'missing'; }, t => { t.themes.light.text = 'layout.panelGap'; },
        t => { delete t.themes.dark.text; }, t => { t.units = 'pixels'; }]) {
        const design = fixture(); const path = join(design, 'themes.json'); const themes = JSON.parse(readFileSync(path));
        mutation(themes); writeFileSync(path, JSON.stringify(themes));
        assert.notEqual(generate(design, join(temporary, 'bad-theme')).status, 0);
    }
});

test('check mode detects stale source, missing output and manual resource edits', () => {
    const design = fixture(); const output = join(temporary, 'check'); success(generate(design, output));
    const path = join(output, files[0]); const original = readFileSync(path);
    writeFileSync(path, '// stale\n'); assert.notEqual(generate(design, output, true).status, 0);
    writeFileSync(path, original); rmSync(join(output, files[1])); assert.notEqual(generate(design, output, true).status, 0);
    success(generate(design, output));
    const palette = join(design, 'tokens/palette.json'); writeFileSync(palette, readFileSync(palette, 'utf8').replace('#2F8CFF', '#123456'));
    assert.notEqual(generate(design, output, true).status, 0);
});


test('localized copy and gallery reject incomplete locale and contract coverage', () => {
    for (const mutate of [c => { delete c.locales.ru.library; }, c => { c.locales.en.library = ''; }, c => { c.locales.en.library = '\b'; },
        c => { c.gallery.states.pop(); }, c => { c.gallery.actions.push('invented'); }]) {
        const design = fixture(); const path = join(design, 'copy.json'); const copy = JSON.parse(readFileSync(path));
        mutate(copy); writeFileSync(path, JSON.stringify(copy));
        assert.notEqual(generate(design, join(temporary, 'bad-copy')).status, 0);
    }
});

test('copy preserves quotes, newlines and Kotlin dollar literals in all generated clients', () => {
    const design = fixture(); const path = join(design, 'copy.json'); const copy = JSON.parse(readFileSync(path));
    copy.locales.en.library = 'A "title"\n$room \\ path'; writeFileSync(path, JSON.stringify(copy));
    const output = join(temporary, 'escaped'); success(generate(design, output));
    for (const file of [files[0], files[1]]) assert.ok(readFileSync(join(output, file), 'utf8').includes(JSON.stringify(copy.locales.en.library)));
    assert.ok(readFileSync(join(output, files[4]), 'utf8').includes(JSON.stringify(copy.locales.en.library).replace(/\$/g, '\\$')));
    const manifest = JSON.parse(readFileSync(join(output, files[3])));
    assert.deepEqual(manifest.copy, copy);
});


test('settings schema rejects invalid defaults, metadata, options and component drift', () => {
    for (const mutate of [s => { s.defaults.language = 'fr'; }, s => { s.sections.reverse(); },
        s => { s.fields[0].options.push('fr'); }, s => { s.fields.push(s.fields[0]); },
        s => { s.fields[0].kind = 'unknown'; }, s => { s.fields[0].label = 'missing'; },
        s => { s.fields[0].maxLength = 0; }, s => { s.defaults.extra = ''; }, s => { s.defaults.username = '\x7f'; }]) {
        const design = fixture(); const path = join(design, 'settings.json'); const spec = JSON.parse(readFileSync(path));
        mutate(spec); writeFileSync(path, JSON.stringify(spec));
        assert.notEqual(generate(design, join(temporary, 'bad-settings')).status, 0);
    }
});

test('settings fields and defaults agree in all native metadata and the hashed manifest', () => {
    const design = join(root, 'packages/design'); const output = join(temporary, 'settings'); success(generate(design, output));
    const spec = JSON.parse(readFileSync(join(design, 'settings.json')));
    const manifest = JSON.parse(readFileSync(join(output, files[3]))); assert.deepEqual(manifest.settings, spec);
    for (const field of spec.fields) {
        for (const file of [files[0], files[1], files[4]]) {
            const source = readFileSync(join(output, file), 'utf8');
            for (const value of [field.id, field.section, field.kind, field.label, ...field.options]) assert.ok(source.includes(JSON.stringify(value)), file + ': ' + value);
            assert.ok(source.includes(JSON.stringify(spec.defaults[field.id])), file + ': default');
        }
    }
});

test('one declarative composition and its source digest reach every client', () => {
    const design = fixture(); const path = join(design, 'layouts.json'); const spec = JSON.parse(readFileSync(path));
    spec.components.Library.kind = 'overlay'; spec.components.Library.props.gap = '23';
    spec.components.Library.children.reverse(); writeFileSync(path, JSON.stringify(spec));
    const output = join(temporary, 'layouts'); success(generate(design, output));
    const manifest = JSON.parse(readFileSync(join(output, files[3])));
    assert.deepEqual(manifest.layouts, spec);
    const digest = createHash('sha256').update(JSON.stringify(spec)).digest('hex');
    for (const file of [files[0], files[1], files[4]]) {
        const source = readFileSync(join(output, file), 'utf8');
        assert.ok(source.includes('SyncWatchLayouts') && source.includes('DesignNode') && source.includes(digest));
        const first = spec.components.Library.children[0].id, last = spec.components.Library.children.at(-1).id;
        assert.ok(source.includes(first + ', ' + last), file + ': shared child ordering');
        assert.ok(source.includes('"overlay"') && source.includes('"23"'));
    }
});

test('declarative document rejects malformed structure, references and visual rules', () => {
    const mutations = [s => s.components.Call.children[1].props.gestureMode = 'unknown', s => s.components.Library.kind = 'nativeLibrary', s => s.components.Library.props.gap = 'NaN',
        s => s.components.Library.props.gap = '@missing.token', s => s.components.Library.props.background = '@layout.panelGap',
        s => s.components.Library.props.background = 'theme.missing', s => s.components.Library.props.unknownStyle = '1',
        s => s.components.Library.props.height = '= 1 +', s => s.components.Library.children[0].id = s.components.Library.id,
        s => s.components.Library.styles.push('missing'), s => s.assets['symbols.playMark'][0][0] = -1,
        s => s.components.Library = {id:'recursive',kind:'include',styles:[],props:{component:'Library'},children:[]},
        s => s.components.Application.children[0].children = [],
        s => delete s.components.Application.children[0].props.presented,
        s => Object.assign(s.components.Library.props,{popoverSlot:'menu',popoverEdge:'top',popoverDismiss:'dismiss'}),
        s => Object.assign(s.components.Library.children[0].props,{popoverSlot:'invalid slot',popoverEdge:'top',popoverDismiss:'dismiss'})];
    for (const change of mutations) {
        const design = fixture(), path = join(design, 'layouts.json'), spec = JSON.parse(readFileSync(path));
        change(spec); writeFileSync(path, JSON.stringify(spec));
        assert.notEqual(generate(design, join(temporary, 'invalid-layout')).status, 0);
    }
});

test('call states, preview cases, action arities and first-party icons reach every client', () => {
    const spec = JSON.parse(readFileSync(join(root, 'packages/design/call.json')));
    const output = join(temporary, 'call'); success(generate(join(root, 'packages/design'), output));
    assert.deepEqual(JSON.parse(readFileSync(join(output, files[3]))).call, spec);
    assert.equal(spec.galleryCases.length, 10);
    for (const file of [files[0], files[1], files[4]]) {
        const source = readFileSync(join(output, file), 'utf8');
        assert.ok(source.includes('SyncWatchCall') && source.includes('CallGalleryCase'));
        for (const value of [...spec.states, ...spec.previewStates, ...Object.keys(spec.actions), ...Object.values(spec.actions), ...Object.keys(spec.icons), ...spec.galleryCases.map(c => c.id)]) assert.ok(source.includes(JSON.stringify(value)), file + ': ' + value);
    }
});

test('call metadata rejects protocol drift, unsupported arities and malformed icon geometry', () => {
    for (const change of [s => s.states.reverse(), s => s.previewStates.pop(), s => s.actions.pin = 'boolean',
        s => s.actions.extra = 'none', s => s.maximumExtent = 0, s => s.galleryCases[0].id = 'unknown',
        s => s.galleryCases[0].state = 'joined', s => s.galleryCases[0].preview = 'unknown',
        s => s.icons.volume[0][0] = 21, s => s.icons.camera[0].pop(), s => s.icons.close = [], s => s.icons.extra = [[0,0,1,1,2,2]]]) {
        const design = fixture(); const path = join(design, 'call.json'); const spec = JSON.parse(readFileSync(path));
        change(spec); writeFileSync(path, JSON.stringify(spec));
        assert.notEqual(generate(design, join(temporary, 'bad-call')).status, 0);
    }
});


test('selection menu scope, identity, stale-input and UTF-8 bounds agree with shared cases', () => {
    const spec = JSON.parse(readFileSync(join(root, 'packages/design/selection-menu.json')));
    const corpus = JSON.parse(readFileSync(join(root, 'packages/design/tests/fixtures/selection-menu.json')));
    assert.equal(corpus.cases.length, 32);
    for (const item of corpus.cases) {
        assert.equal(validSelection(spec, item.presentation), item.valid, item.name);
        const before = JSON.stringify(item.presentation);
        const received = item.requests.map(r => selectionRequest(spec, item.presentation, r.entryID, r.contextToken)).filter(Boolean);
        assert.deepEqual(received, item.expected, item.name);
        assert.equal(JSON.stringify(item.presentation), before);
    }
});

test('selection metadata reaches all clients and rejects scope or bounds drift', () => {
    const spec = JSON.parse(readFileSync(join(root, 'packages/design/selection-menu.json')));
    const output = join(temporary, 'selection-menu'); success(generate(join(root, 'packages/design'), output));
    assert.deepEqual(JSON.parse(readFileSync(join(output, files[3]))).selectionMenu, spec);
    for (const file of [files[0], files[1], files[4]]) {
        const source = readFileSync(join(output, file), 'utf8');
        assert.ok(source.includes('SyncWatchSelectionMenu') && source.includes('SelectionMenuKind'));
        for (const kind of spec.kinds) for (const value of Object.values(kind)) assert.ok(source.includes(JSON.stringify(value)), file + ': ' + value);
    }
    for (const change of [s => s.kinds[1].scope = 'shared', s => s.kinds[0].action = 'seek', s => s.kinds.pop(),
        s => s.maximumEntries = 0, s => s.maximumRevision = Number.MAX_SAFE_INTEGER + 1, s => s.maximumLabelBytes = 1,
        s => s.galleryEntryCount = s.maximumEntries + 1, s => s.states.reverse()]) {
        const design = fixture(), path = join(design, 'selection-menu.json'), changed = structuredClone(spec);
        change(changed); writeFileSync(path, JSON.stringify(changed));
        assert.notEqual(generate(design, join(temporary, 'invalid-selection')).status, 0);
    }
});

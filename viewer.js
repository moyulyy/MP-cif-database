/* MP-CIF 3D renderer: receives CIF text from Python over QWebChannel. */
'use strict';
let viewer, bridge, model = null;
let current = { generation: -1, cif: null, style: 'ball', showCell: true, label: '' };
const COLOR_FALLBACK = '#a0a9b8';

function reportError(message) {
    const hint = document.getElementById('hint');
    hint.textContent = '渲染错误：' + message;
    if (bridge) bridge.reportError(String(message));
}
window.addEventListener('error', event => reportError(event.message));

function atomStyle() {
    switch (current.style) {
        case 'space': return { sphere: { scale: 1.0 } };
        case 'line': return { line: { linewidth: 2 } };
        case 'stick': return { stick: { radius: 0.15 } };
        default: return { stick: { radius: 0.12 }, sphere: { scale: 0.26 } };
    }
}

function legendFor() {
    const legend = document.getElementById('legend');
    const entries = document.getElementById('legend-entries');
    entries.replaceChildren();
    if (!model) { legend.hidden = true; return; }
    try {
        const atoms = model.selectedAtoms({});
        const counts = new Map();
        for (const atom of atoms) {
            const elem = atom.elem || 'X';
            counts.set(elem, (counts.get(elem) || 0) + 1);
        }
        if (!counts.size) { legend.hidden = true; return; }
        legend.hidden = false;
        const colors = ($3Dmol.elementColors && $3Dmol.elementColors.Jmol) || {};
        for (const [elem, count] of [...counts.entries()].sort()) {
            const row = document.createElement('div');
            row.className = 'legend-row';
            const ball = document.createElement('span');
            ball.className = 'legend-ball';
            ball.style.backgroundColor = colors[elem] || COLOR_FALLBACK;
            const name = document.createElement('strong');
            name.textContent = elem;
            const detail = document.createElement('span');
            detail.className = 'legend-detail';
            detail.textContent = String(count);
            row.append(ball, name, detail);
            entries.append(row);
        }
    } catch (error) { reportError(error.message); }
}

function drawCell() {
    if (!current.showCell || !model) return;
    try { viewer.addUnitCell(model, { color: '#a7b5c8', linewidth: 1.5 }); }
    catch (error) { /* not all models have a cell */ }
}

function render() {
    if (!model) return;
    viewer.removeAllShapes();
    viewer.setStyle({}, atomStyle());
    drawCell();
    viewer.render();
    legendFor();
}

function loadCif(cif, label) {
    current.cif = cif;
    current.label = label || '';
    viewer.removeAllModels();
    viewer.removeAllShapes();
    model = viewer.addModel(cif, 'cif');
    document.getElementById('empty').style.display = 'none';
    document.getElementById('caption').innerHTML = '';
    if (label) {
        const strong = document.createElement('b');
        strong.textContent = label;
        document.getElementById('caption').appendChild(strong);
    }
    render();
    viewer.zoomTo();
    viewer.rotate(18, 'x');
    viewer.rotate(-22, 'y');
    viewer.zoom(0.92);
    viewer.render();
}

function applyState(state) {
    try {
        current.style = state.style || current.style;
        current.showCell = state.showCell !== undefined ? state.showCell : current.showCell;
        if (state.generation !== undefined) current.generation = state.generation;
        if (state.cif) { loadCif(state.cif, state.label); return; }
        render();
    } catch (error) { reportError(error.stack || error.message); }
}

window.addEventListener('resize', () => { if (viewer) { viewer.resize(); viewer.render(); } });

try {
    viewer = $3Dmol.createViewer(document.getElementById('viewer'),
        { backgroundColor: '#FFFFFF', antialias: true });
    if (typeof qt !== 'undefined') {
        new QWebChannel(qt.webChannelTransport, channel => {
            bridge = channel.objects.bridge;
            bridge.stateChanged.connect(json => applyState(JSON.parse(json)));
            bridge.command.connect(command => {
                if (!model) return;
                if (command === 'reset') { viewer.zoomTo(); viewer.rotate(18, 'x'); viewer.rotate(-22, 'y'); viewer.render(); }
                else if (command === 'zoomIn') { viewer.zoom(1.15); viewer.render(); }
                else if (command === 'zoomOut') { viewer.zoom(0.87); viewer.render(); }
            });
            bridge.ready();
        });
    } else {
        document.getElementById('hint').textContent = '请通过 app.py 打开此视图';
    }
} catch (error) { reportError(error.message); }

/* Uplix admin charts and interactions.
 *
 * No libraries and no network calls: every chart below is plain SVG built from the JSON that
 * the server already rendered into the page. Charts read their numbers from the DOM only.
 */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var C = {
    brand: '#2f5bea',
    brandSoft: '#e8eefe',
    coupon: '#0e7a63',
    control: '#5b4bb8',
    traditional: '#b9a3e3',
    warn: '#a35a00',
    ink: '#2c3648',
    muted: '#6b7789',
    line: '#d8dee8',
    grid: '#eef1f6',
    paper: '#ffffff'
  };

  function node(name, attrs, text) {
    var n = document.createElementNS(NS, name);
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] !== null && attrs[k] !== undefined) {
          n.setAttribute(k, attrs[k]);
        }
      }
    }
    if (text !== null && text !== undefined) { n.textContent = text; }
    return n;
  }

  function canvas(width, height) {
    return node('svg', {
      viewBox: '0 0 ' + width + ' ' + height,
      width: '100%',
      preserveAspectRatio: 'xMidYMid meet',
      role: 'img'
    });
  }

  function clear(host) {
    if (!host) { return false; }
    while (host.firstChild) { host.removeChild(host.firstChild); }
    return true;
  }

  function fmt(n) {
    if (n === null || n === undefined || isNaN(n)) { return '—'; }
    return Number(n).toLocaleString();
  }

  function pct(x, digits) {
    if (x === null || x === undefined || isNaN(x)) { return '—'; }
    return (100 * x).toFixed(digits === undefined ? 1 : digits) + '%';
  }

  function niceMax(value) {
    if (!isFinite(value) || value <= 0) { return 1; }
    var mag = Math.pow(10, Math.floor(Math.log(value) / Math.LN10));
    var scaled = value / mag;
    var step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
    return step * mag;
  }

  function label(svg, x, y, text, anchor, fill, size) {
    svg.appendChild(node('text', {
      x: x, y: y, 'text-anchor': anchor || 'middle',
      fill: fill || C.muted, 'font-size': size || 11
    }, text));
  }

  /* ---------------------------------------------------------------- funnel */
  function funnel(host, data) {
    if (!clear(host)) { return; }
    var stages = [
      ['view_item', 'Viewed'], ['add_to_cart', 'Cart'],
      ['checkout_start', 'Checkout'], ['order_placed', 'Ordered']
    ];
    var W = 640, H = 250, L = 42, R = 12, T = 22, B = 40;
    var innerW = W - L - R, innerH = H - T - B;
    var max = 1;
    stages.forEach(function (stage) {
      [1, 2].forEach(function (round) {
        var v = ((data[round] || {})[stage[0]]) || 0;
        if (v > max) { max = v; }
      });
    });
    max = niceMax(max);
    var svg = canvas(W, H);
    var y = function (v) { return T + innerH - (v / max) * innerH; };

    [0, 0.5, 1].forEach(function (f) {
      var yy = T + innerH - f * innerH;
      svg.appendChild(node('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid-line' }));
      label(svg, L - 8, yy + 4, fmt(Math.round(max * f)), 'end');
    });

    var groupW = innerW / stages.length;
    var barW = Math.min(30, groupW * 0.28);
    stages.forEach(function (stage, i) {
      var cx = L + groupW * (i + 0.5);
      [1, 2].forEach(function (round, j) {
        var value = ((data[round] || {})[stage[0]]) || 0;
        var top = y(value);
        var x = cx - barW - 2 + j * (barW + 4);
        svg.appendChild(node('rect', {
          x: x, y: top, width: barW, height: Math.max(0, T + innerH - top),
          rx: 3, fill: round === 1 ? C.brand : C.coupon
        }));
        if (value > 0) {
          label(svg, x + barW / 2, top - 5, fmt(value), 'middle', C.ink, 10);
        }
      });
      label(svg, cx, H - 16, stage[1], 'middle', C.muted, 11);
    });

    svg.appendChild(node('line', { x1: L, x2: W - R, y1: T + innerH, y2: T + innerH, class: 'axis' }));
    label(svg, L, H - 2, 'Round 1', 'start', C.brand, 11);
    label(svg, L + 62, H - 2, 'Round 2', 'start', C.coupon, 11);
    host.appendChild(svg);
  }

  /* ---------------------------------------------------------------- timeline */
  function timeline(host, buckets, bounds) {
    if (!clear(host)) { return; }
    var W = 640, H = 250, L = 42, R = 14, T = 26, B = 36;
    var innerW = W - L - R, innerH = H - T - B;
    if (!buckets || !buckets.length) {
      host.appendChild(node('p', { class: 'muted small' }, 'No orders yet.'));
      return;
    }
    var byBucket = {};
    buckets.forEach(function (b) {
      var key = b.epoch;
      if (!byBucket[key]) { byBucket[key] = { 1: 0, 2: 0 }; }
      byBucket[key][b.round] = b.n;
    });
    var keys = Object.keys(byBucket).map(Number).sort(function (a, b) { return a - b; });
    var minE = keys[0];
    var maxE = keys[keys.length - 1] + 300;
    var span = Math.max(300, maxE - minE);
    var x = function (epoch) { return L + ((epoch - minE) / span) * innerW; };
    var max = 1;
    keys.forEach(function (k) {
      var total = byBucket[k][1] + byBucket[k][2];
      if (total > max) { max = total; }
    });
    max = niceMax(max);
    var y = function (v) { return T + innerH - (v / max) * innerH; };

    var svg = canvas(W, H);

    (bounds || []).forEach(function (b) {
      if (b.start === null || b.start === undefined) { return; }
      var x1 = Math.max(L, x(b.start));
      var x2 = Math.min(W - R, x(b.finish));
      svg.appendChild(node('rect', {
        x: x1, y: T - 8, width: Math.max(1, x2 - x1), height: innerH + 8,
        class: 'round-shade', rx: 3
      }));
      if (x2 - x1 > 34) {
        label(svg, (x1 + x2) / 2, T - 12, 'Round ' + b.round, 'middle', '#9aa6b8', 10);
      }
    });

    [0, 0.5, 1].forEach(function (f) {
      var yy = y(max * f);
      svg.appendChild(node('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid-line' }));
      label(svg, L - 8, yy + 4, fmt(Math.round(max * f)), 'end');
    });

    var barW = Math.max(2, Math.min(22, (300 / span) * innerW * 0.82));
    keys.forEach(function (k) {
      var base = T + innerH;
      [1, 2].forEach(function (round) {
        var v = byBucket[k][round];
        if (!v) { return; }
        var height = (v / max) * innerH;
        svg.appendChild(node('rect', {
          x: x(k) - barW / 2, y: base - height, width: barW, height: height,
          rx: 1, fill: round === 1 ? C.brand : C.coupon
        }));
        base -= height;
      });
    });

    svg.appendChild(node('line', { x1: L, x2: W - R, y1: T + innerH, y2: T + innerH, class: 'axis' }));
    label(svg, L, H - 12, new Date(minE * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), 'start');
    label(svg, W - R, H - 12, new Date(maxE * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), 'end');
    host.appendChild(svg);
  }

  /* ---------------------------------------------------------------- doughnut */
  function doughnut(host, rows) {
    if (!clear(host)) { return; }
    var palette = ['#2f5bea', '#0e7a63', '#c98a1b', '#5b4bb8'];
    var total = rows.reduce(function (sum, row) { return sum + row.n; }, 0);
    var W = 420, H = 250, cx = 130, cy = 125, outer = 92, inner = 56;
    var svg = canvas(W, H);

    if (!total) {
      label(svg, cx, cy, 'No customers', 'middle', C.muted, 13);
      host.appendChild(svg);
      return;
    }

    var source = new URLSearchParams(window.location.search).get('source') || 'real';
    var angle = -Math.PI / 2;
    rows.forEach(function (row, i) {
      if (!row.n) { return; }
      var sweep = (row.n / total) * Math.PI * 2;
      var end = angle + sweep;
      var large = sweep > Math.PI ? 1 : 0;
      var p = [
        'M', cx + outer * Math.cos(angle), cy + outer * Math.sin(angle),
        'A', outer, outer, 0, large, 1, cx + outer * Math.cos(end), cy + outer * Math.sin(end),
        'L', cx + inner * Math.cos(end), cy + inner * Math.sin(end),
        'A', inner, inner, 0, large, 0, cx + inner * Math.cos(angle), cy + inner * Math.sin(angle),
        'Z'
      ].join(' ');
      var slice = node('path', { d: p, fill: palette[i % palette.length], class: 'slice' });
      slice.appendChild(node('title', null, row.tag + ': ' + row.n + ' customers'));
      slice.addEventListener('click', function () {
        window.location.href = '/admin/customers?source=' + encodeURIComponent(source) +
          '&tag=' + encodeURIComponent(row.tag);
      });
      svg.appendChild(slice);
      angle = end;

      var mid = (angle - sweep / 2);
      var lx = cx + (outer + 18) * Math.cos(mid);
      var ly = cy + (outer + 18) * Math.sin(mid);
      if (W - lx > 20) {
        label(svg, lx, ly + 4, row.tag + ' ' + row.n, lx > cx ? 'start' : 'end', C.ink, 11);
      }
    });
    label(svg, cx, cy - 2, fmt(total), 'middle', C.ink, 22);
    label(svg, cx, cy + 16, 'customers', 'middle', C.muted, 10);
    host.appendChild(svg);
  }

  /* ---------------------------------------------------------------- power */
  function powerFormula(baseline, target) {
    if (!(baseline > 0 && baseline < 1 && target > 0 && target < 1) || baseline === target) {
      return 0;
    }
    var zA = 1.959963985, zB = 0.8416212336;
    var gap = Math.abs(target - baseline);
    var variance = baseline * (1 - baseline) + target * (1 - target);
    return Math.ceil(Math.pow(zA + zB, 2) * variance / (gap * gap));
  }

  function power(inputA, inputB, output) {
    if (!inputA || !inputB || !output) { return; }
    function update() {
      var a = parseFloat(inputA.value) / 100;
      var b = parseFloat(inputB.value) / 100;
      var n = powerFormula(a, b);
      output.textContent = n ? String(n) : '—';
    }
    inputA.addEventListener('input', update);
    inputB.addEventListener('input', update);
    update();
  }

  /* ---------------------------------------------------------------- lane */
  function laneClass(lane) {
    var key = String(lane || '').toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-+|-+$/g, '');
    var aliases = { 'treat': 'persuadable', 'do-not-disturb': 'sleeping-dog', 'skip': 'skip' };
    key = aliases[key] || key;
    var known = ['sure-thing', 'persuadable', 'lost-cause', 'sleeping-dog', 'skip'];
    return known.indexOf(key) >= 0 ? key : 'skip';
  }

  /* ---------------------------------------------------------------- horizontal bars */
  function horizontalBars(host, rows, options) {
    if (!clear(host)) { return; }
    options = options || {};
    var W = 640, rowH = options.rowHeight || 34, L = options.labelWidth || 210;
    var R = 54, T = 8, B = 8;
    var H = T + B + rows.length * rowH;
    var innerW = W - L - R;
    var max = 1;
    rows.forEach(function (row) { if (row.hi > max) { max = row.hi; } });
    max = niceMax(max);
    var svg = canvas(W, H);

    rows.forEach(function (row, i) {
      var cy = T + i * rowH + rowH / 2;
      label(svg, L - 12, cy + 4, row.name, 'end', C.ink, 11);
      var width = Math.max(0, (row.value / max) * innerW);
      var negative = row.value < 0;
      svg.appendChild(node('rect', {
        x: L, y: cy - 9, width: width, height: 18, rx: 3,
        fill: options.negative ? (negative ? '#b42318' : C.brand) : (row.fill || C.brand)
      }));
      if (row.lo !== null && row.hi !== null && row.lo !== undefined && row.hi !== undefined) {
        var x1 = L + (Math.max(0, row.lo) / max) * innerW;
        var x2 = L + (Math.max(0, row.hi) / max) * innerW;
        x1 = Math.min(x1, L + innerW);
        x2 = Math.min(x2, L + innerW);
        svg.appendChild(node('line', { x1: x1, x2: x2, y1: cy, y2: cy, class: 'whisker' }));
        svg.appendChild(node('line', { x1: x1, x2: x1, y1: cy - 6, y2: cy + 6, class: 'cap' }));
        svg.appendChild(node('line', { x1: x2, x2: x2, y1: cy - 6, y2: cy + 6, class: 'cap' }));
      }
      label(svg, W - R + 8, cy + 4,
        (options.signed ? (row.value >= 0 ? '+' : '') : '') + Math.round(row.value), 'start', C.muted, 11);
    });

    svg.appendChild(node('line', { x1: L, x2: L, y1: T, y2: T + rows.length * rowH, class: 'axis' }));
    host.appendChild(svg);
  }

  /* ---------------------------------------------------------------- line charts */
  function lineChart(host, series, options) {
    if (!clear(host)) { return; }
    options = options || {};
    var W = 640, H = options.height || 250, L = 52, R = 16, T = 16, B = 42;
    var innerW = W - L - R, innerH = H - T - B;
    var xs = [], ys = [];
    series.forEach(function (s) {
      s.points.forEach(function (p) {
        xs.push(p[0]);
        ys.push(p[1]);
        if (p[2] !== undefined) { ys.push(p[2]); }
      });
    });
    if (!xs.length) { return; }
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    if (minY > 0 && options.startAtZero !== false) { minY = 0; }
    if (minY === maxY) { maxY = minY + 1; }
    var pad = (maxY - minY) * 0.08;
    maxY += pad;
    minY = options.startAtZero === false ? minY - pad : Math.min(0, minY);

    var x = function (v) { return L + ((v - minX) / (maxX - minX || 1)) * innerW; };
    var y = function (v) { return T + innerH - ((v - minY) / (maxY - minY)) * innerH; };

    var svg = canvas(W, H);
    [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
      var value = minY + (maxY - minY) * f;
      var yy = y(value);
      svg.appendChild(node('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid-line' }));
      label(svg, L - 8, yy + 4, options.yFormat ? options.yFormat(value) : Math.round(value), 'end');
    });
    [0, 0.5, 1].forEach(function (f) {
      var v = minX + (maxX - minX) * f;
      label(svg, x(v), H - 18, options.xFormat ? options.xFormat(v) : v, 'middle');
    });

    series.forEach(function (s) {
      if (s.band) {
        var top = s.band.map(function (p) { return [x(p[0]), y(p[1])]; });
        var bottom = s.band.slice().reverse().map(function (p) { return [x(p[0]), y(p[2])]; });
        var d = 'M' + top.map(function (p) { return p[0] + ',' + p[1]; }).join(' L') +
          ' L' + bottom.map(function (p) { return p[0] + ',' + p[1]; }).join(' L') + ' Z';
        svg.appendChild(node('path', { d: d, class: 'area' }));
      }
      var path = 'M' + s.points.map(function (p) { return x(p[0]) + ',' + y(p[1]); }).join(' L');
      svg.appendChild(node('path', { d: path, class: 'line ' + (s.className || 'uplift') }));
    });

    svg.appendChild(node('line', { x1: L, x2: W - R, y1: T + innerH, y2: T + innerH, class: 'axis' }));
    if (options.xLabel) { label(svg, W / 2, H - 2, options.xLabel, 'middle'); }
    if (options.yLabel) {
      label(svg, 14, T + innerH / 2, options.yLabel, 'middle');
    }
    host.appendChild(svg);
  }

  /* ---------------------------------------------------------------- model page */
  function modelPage(graphs, els) {
    var coupons = Object.keys(graphs.by_coupon || {}).sort(function (a, b) { return Number(a) - Number(b); });
    if (!coupons.length) { return; }

    function draw() {
      var key = (els.select && els.select.value) || coupons[0];
      if (!graphs.by_coupon[key]) { key = coupons[0]; }
      var block = graphs.by_coupon[key];

      horizontalBars(els.strategy, block.strategies.map(function (s) {
        return { name: s.name, value: s.value, lo: s.lo, hi: s.hi, fill: /Uplift/i.test(s.name) ? C.brand : '#aebbd0' };
      }), { labelWidth: 200, rowHeight: 38 });

      if (els.differences) {
        horizontalBars(els.differences, block.differences.map(function (s) {
          return { name: s.name, value: s.value, lo: s.lo, hi: s.hi };
        }), { labelWidth: 200, rowHeight: 34, signed: true });
      }

      if (els.verdict) {
        var reliable = String(block.verdict || '').toLowerCase() === 'reliable';
        els.verdict.innerHTML = '<span class="badge ' + (reliable ? 'reliable' : 'inconclusive') + '">' +
          (block.verdict || 'unknown') + '</span> ' +
          (block.beats_traditional ? 'The uplift model beats the traditional ranking.' : 'The uplift model does not beat the traditional ranking.') +
          ' Best treated share: <b>' + Math.round(100 * block.f_star) + '%</b>.';
      }

      var curve = block.budget_curve;
      var fraction = curve.fractions;
      var upliftPoints = fraction.map(function (f, i) { return [f, curve.uplift.value[i]]; });
      var traditionalPoints = fraction.map(function (f, i) { return [f, curve.traditional.value[i]]; });
      var randomPoints = fraction.map(function (f, i) { return [f, curve.random.value[i]]; });

      lineChart(els.budget, [
        { points: randomPoints, className: 'traditional' },
        { points: traditionalPoints, className: 'traditional' },
        {
          points: upliftPoints, className: 'uplift',
          band: fraction.map(function (f, i) { return [f, curve.uplift.hi[i], curve.uplift.lo[i]]; })
        }
      ], {
        height: 240,
        xFormat: function (v) { return Math.round(100 * v) + '%'; },
        yFormat: function (v) { return Math.round(v); },
        xLabel: 'Share of customers treated'
      });

      updateSlider();

      function updateSlider() {
        if (!els.slider) { return; }
        var idx = Math.round((parseFloat(els.slider.value) / 100) * (fraction.length - 1));
        idx = Math.max(0, Math.min(fraction.length - 1, idx));
        var f = fraction[idx];
        if (els.budgetValue) { els.budgetValue.textContent = Math.round(100 * f) + '%'; }
        if (els.budgetReadout) {
          els.budgetReadout.innerHTML =
            'Random ' + Math.round(curve.random.value[idx]) +
            ' &middot; Traditional ' + Math.round(curve.traditional.value[idx]) +
            ' &middot; Uplift <b>' + Math.round(curve.uplift.value[idx]) + '</b>' +
            ' (interval ' + Math.round(curve.uplift.lo[idx]) + ' to ' + Math.round(curve.uplift.hi[idx]) + ')';
        }
      }
      if (els.slider && !els.slider.dataset.bound) {
        els.slider.addEventListener('input', updateSlider);
        els.slider.dataset.bound = '1';
      }
    }

    if (els.qini && graphs.qini) {
      var uplift = graphs.qini.uplift;
      var traditional = graphs.qini.traditional;
      lineChart(els.qini, [
        {
          points: traditional.x.map(function (xv, i) { return [xv, traditional.y[i]]; }),
          className: 'traditional'
        },
        {
          points: uplift.x.map(function (xv, i) { return [xv, uplift.y[i]]; }),
          className: 'uplift'
        }
      ], {
        height: 240,
        xFormat: function (v) { return Math.round(100 * v) + '%'; },
        yFormat: function (v) { return Math.round(v); },
        xLabel: 'Share of customers targeted',
        startAtZero: false
      });
    }

    if (els.select) {
      els.select.addEventListener('change', draw);
    }
    draw();
  }

  window.UplixAdmin = {
    funnel: funnel,
    timeline: timeline,
    doughnut: doughnut,
    power: power,
    laneClass: laneClass,
    modelPage: modelPage,
    powerFormula: powerFormula
  };
})();

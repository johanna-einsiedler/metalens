import * as d3 from 'npm:d3'

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function truncateLabel(label, maxChars) {
  const text = String(label ?? "");
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(0, maxChars - 3))}...`;
}

// function to draw the Graph
export const drawGraph = item => {
  let data = JSON.parse(JSON.stringify(item));
  d3.select('#chartArea').selectAll('*').remove();

  if (!Array.isArray(data) || data.length === 0) {
    return {width: 0, height: 0};
  }

  const chartArea = document.getElementById('chartArea');
  const availableWidth = chartArea ? chartArea.clientWidth : 600;
  const isMobile = window.matchMedia('(max-width: 768px)').matches;

  const studyLabelColumn = isMobile
    ? clamp(Math.round(availableWidth * 0.34), 140, 220)
    : 230;
  const statsColumn = isMobile
    ? clamp(Math.round(availableWidth * 0.24), 110, 170)
    : 160;

  const margin = {
    top: isMobile ? 64 : 80,
    right: 20,
    bottom: isMobile ? 90 : 100,
    left: studyLabelColumn + statsColumn + (isMobile ? 28 : 20)
  };

  const minPlotAreaWidth = isMobile ? 320 : 460;
  const width = Math.max(availableWidth, margin.left + margin.right + minPlotAreaWidth);

  const rowHeight = isMobile ? 30 : 22;
  const minHeight = isMobile ? 540 : 520;
  const height = Math.max(minHeight, margin.top + margin.bottom + (data.length + 3) * rowHeight);

  const innerWidth = width - margin.right - margin.left;
  const innerHeight = height - margin.top - margin.bottom;
  const studyLabelX = -margin.left + 8;
  const statsLabelX = -margin.left + studyLabelColumn + 8;

  data.sort((a, b) => a.yi - b.yi);

  data.forEach(obj => {
    obj.ci_lower = obj.yi - Math.sqrt(obj.vi);
    obj.ci_upper = obj.yi + Math.sqrt(obj.vi);
    obj.n = 5;
  });

  const xMaxRaw = d3.max(data, d => d.ci_upper);
  const xMinRaw = d3.min(data, d => d.ci_lower);
  const domainPad = xMaxRaw === xMinRaw ? Math.max(0.1, Math.abs(xMaxRaw || 0) * 0.1) : 0;
  const xMax = xMaxRaw + domainPad;
  const xMin = xMinRaw - domainPad;

  let i = 0;
  let nom = 0;
  let denom = 0;
  let WY = 0;
  let WY2 = 0;
  let W = 0;
  let W2 = 0;

  while (i < data.length) {
    WY = WY + data[i].yi * (1 / data[i].vi);
    W = W + 1 / data[i].vi;
    WY2 = WY2 + Math.pow(data[i].yi, 2) * (1 / data[i].vi);
    i++;
  }

  const k = data.length;
  const FF = WY / W;
  const FF_VI = 1 / W;

  Object.assign(data, {
    [k]: {
      id: 'Fixed effects model',
      yi: FF,
      vi: FF_VI,
      ci_lower: FF - Math.sqrt(FF_VI),
      ci_upper: FF + Math.sqrt(FF_VI)
    }
  });

  const df = k - 1;
  const Q = WY2 - Math.pow(WY, 2) / W;
  const C = W - W2 / W;
  const T2 = (Q - df) / C;

  nom = 0;
  denom = 0;
  i = 0;
  while (i < data.length) {
    nom = nom + data[i].yi * (1 / (data[i].vi + T2));
    denom = denom + 1 / (data[i].vi + T2);
    i++;
  }

  const RF = nom / denom;
  const RF_VI = Math.sqrt(1 / denom);

  data = Object.assign(data, {
    [k + 1]: {
      id: 'Random effects model',
      yi: RF,
      vi: RF_VI,
      ci_lower: RF - Math.sqrt(RF_VI),
      ci_upper: RF + Math.sqrt(RF_VI)
    }
  });

  const xScale = d3
    .scaleLinear()
    .domain([xMin, xMax])
    .range([0, innerWidth]);

  const yScale = d3
    .scalePoint()
    .domain(data.map(d => d.id))
    .range([0, innerHeight])
    .padding(0.9);

  const widthScale = d3
    .scaleLinear()
    .domain(d3.extent(data, d => d.n))
    .range(isMobile ? [8, 12] : [10, 17]);

  const svg = d3
    .select('#chartArea')
    .append('svg')
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', `0 0 ${width} ${height}`)
    .style('align', 'center');

  const plotG = svg
    .attr('id', 'pG')
    .append('g')
    .attr('transform', `translate(${margin.left},${margin.top})`);

  plotG
    .append('line')
    .attr('x1', studyLabelX)
    .attr('x2', innerWidth)
    .attr('stroke', '#555')
    .attr('stroke-width', 2);

  if (xMin <= 0 && xMax >= 0) {
    plotG
      .append('line')
      .attr('transform', `translate(${xScale(0)},0)`)
      .attr('y2', innerHeight)
      .attr('stroke', '#555')
      .attr('stroke-width', 2);
  }

  plotG
    .selectAll('rect')
    .data(data.filter((_, idx) => idx < data.length - 2))
    .join(enter => enter
      .append('rect')
      .classed('heterogeneity-band', true)
      .attr('id', d => d.id)
      .attr('x', d => xScale(d.ci_lower))
      .attr('width', d => xScale(d.ci_upper) - xScale(d.ci_lower))
      .attr('height', innerHeight)
      .attr('opacity', 0.01 + 1 / data.length)
      .attr('fill', '#067')
      .attr('mix-blend-mode', 'multiply')
    );

  const gSub = plotG
    .selectAll('g')
    .data(data.filter((_, idx) => idx < data.length - 2))
    .enter()
    .append('g');

  gSub
    .append('rect')
    .attr('x', d => xScale(d.yi) - widthScale(d.n) / 2)
    .attr('y', d => yScale(d.id) - widthScale(d.n) / 2)
    .attr('width', d => widthScale(d.n))
    .attr('height', d => widthScale(d.n))
    .attr('fill', '#333');

  gSub
    .append('line')
    .attr('transform', d => `translate(0,${yScale(d.id)})`)
    .attr('x1', d => xScale(d.ci_lower))
    .attr('x2', d => xScale(d.ci_upper))
    .attr('stroke', '#333')
    .attr('stroke-width', 2)
    .attr('fill', '#333');

  const textG = gSub.append('g');

  textG
    .append('text')
    .text(d => truncateLabel(d.id, isMobile ? 18 : 42))
    .attr('x', studyLabelX)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')
    .attr('fill', '#222')
    .attr('font-size', isMobile ? '0.72rem' : '0.84rem');

  textG
    .append('text')
    .text(
      d => `${d.yi.toFixed(2)} [ ${d.ci_lower.toFixed(2)} - ${d.ci_upper.toFixed(2)} ]`
    )
    .attr('x', statsLabelX)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')
    .attr('font-size', isMobile ? '0.7rem' : '0.82rem');

  const statOffset = -20;

  plotG
    .append('text')
    .text('Study ID')
    .attr('x', studyLabelX)
    .attr('y', statOffset)
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')
    .attr('font-size', isMobile ? '0.75rem' : '0.85rem');

  plotG
    .append('text')
    .text('[95% CI]')
    .attr('x', statsLabelX)
    .attr('y', statOffset)
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')
    .attr('font-size', isMobile ? '0.75rem' : '0.85rem');

  plotG
    .append('line')
    .attr('transform', `translate(0,${innerHeight})`)
    .attr('x1', studyLabelX)
    .attr('x2', innerWidth)
    .attr('stroke', '#555')
    .attr('stroke-width', 2);

  const xAxis = plotG.append('g');
  const xTicks = d3.ticks(xScale.domain()[0], xScale.domain()[1], isMobile ? 4 : 6);
  const tickFormat = d3.format('.2f');

  xAxis
    .selectAll('text')
    .data(xTicks)
    .enter()
    .append('text')
    .attr('x', d => xScale(d))
    .attr('y', innerHeight + 15)
    .attr('dy', '0.32em')
    .attr('text-anchor', 'middle')
    .attr('font-size', isMobile ? '0.68rem' : '0.8rem')
    .text(d => tickFormat(d));

  xAxis
    .selectAll('line')
    .data(xTicks)
    .enter()
    .append('line')
    .attr('transform', d => `translate(${xScale(d)},${innerHeight - 5})`)
    .attr('y2', 10)
    .attr('stroke', '#555')
    .attr('stroke-width', '2');

  const pooledG = plotG
    .append('g')
    .attr('transform', `translate(0,${yScale.step() / 4})`);

  const pooledGSub = pooledG
    .selectAll('g')
    .data(data.filter((_, idx) => idx >= data.length - 2))
    .enter()
    .append('g');

  pooledGSub
    .append('polygon')
    .attr(
      'points',
      d => `
      ${xScale(d.ci_lower)}, ${yScale(d.id)}
      ${xScale(d.yi)}, ${yScale(d.id) + yScale.step() / 4}
      ${xScale(d.ci_upper)}, ${yScale(d.id)}
      ${xScale(d.yi)}, ${yScale(d.id) - yScale.step() / 4}
    `
    )
    .attr('fill', '#333');

  pooledGSub
    .append('text')
    .text(d => truncateLabel(d.id, isMobile ? 18 : 42))
    .attr('x', studyLabelX)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')
    .attr('font-size', isMobile ? '0.72rem' : '0.84rem');

  pooledGSub
    .append('text')
    .text(
      d => `${d.yi.toFixed(2)} [ ${d.ci_lower.toFixed(2)} - ${d.ci_upper.toFixed(2)} ]`
    )
    .attr('x', statsLabelX)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')
    .attr('font-size', isMobile ? '0.7rem' : '0.82rem');

  plotG
    .append('text')
    .attr('x', studyLabelX)
    .attr('y', innerHeight)
    .attr('dy', '2em')
    .attr('font-size', isMobile ? '0.75rem' : '1em')
    .text(
      `${isMobile ? 'Fixed effect' : 'Estimated Hypermean (Fixed Effects Model)'}: ${data[data.length - 2].yi.toFixed(2)}`
    );

  plotG
    .append('text')
    .attr('x', studyLabelX)
    .attr('y', innerHeight)
    .attr('dy', '3.2em')
    .attr('font-size', isMobile ? '0.75rem' : '1em')
    .text(
      `${isMobile ? 'Random effect' : 'Estimated Hypermean (Random Effects Model)'}: ${data[data.length - 1].yi.toFixed(2)}`
    );

  return {width, height};
}

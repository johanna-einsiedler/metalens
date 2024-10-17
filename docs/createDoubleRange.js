

//https://www.thecoderashok.com/blog/double-range-slider-with-min-max-value -->
//https://medium.com/@predragdavidovic10/native-dual-range-slider-html-css-javascript-91e778134816 -->



import {html} from "htl";
import {maybeWidth} from "./css.js";
import {checkValidity, preventDefault} from "./event.js";
import {formatTrim} from "./format.js";
import {identity} from "./identity.js";
import {maybeLabel} from "./label.js";

const epsilon = 1e-6;

export function doubleRange(extent = [0, 1], options) {
  return createDoubleRange({extent,range: true}, options);
}

export function createDoubleRange({
  extent: [min, max],
  range
}, {
  format = formatTrim,
  transform,
  invert,
  label = "",
  value: initialValue,
  step,
  disabled,
  placeholder,
  validate = checkValidity,
  width
} = {}) {
  let value;
  if (typeof format !== "function") throw new TypeError("format is not a function");
  if (min == null || isNaN(min = +min)) min = -Infinity;
  if (max == null || isNaN(max = +max)) max = Infinity;
  if (min > max) [min, max] = [max, min], transform === undefined && (transform = negate);
  if (step !== undefined) step = +step;
  //if (inputRange !== undefined) {

const maxString = max.toString().length;
const minString = min.toString().length
const longerString = maxString > minString ? maxString : minString;
let numLenLower = String(Math.max(min.toString().length*10+10, 30))+'px'
let numLenUpper = String(Math.max(max.toString().length*10+10, 30))+'px'

const number = html`<input  style="width: ${numLenLower}"; type=number id=numberLower min=${isFinite(min) ? min : null} max=${isFinite(max) ? max : null} step=${step == undefined ? "any" : step} name=numberLower required placeholder=${placeholder} oninput=${onnumber} disabled=${disabled}>`;
const numberUpper = html`<input style="width: ${numLenUpper}"  type=number id=numberUpper min=${isFinite(min) ? min : null} max=${isFinite(max) ? max : null} step=${step == undefined ? "any" : step} name=numberUpper required placeholder=${placeholder} oninput=${onnumber} disabled=${disabled}>`;
console.log('exists?2', window.hasOwnProperty('number'))

  let irange; // untransformed range for coercion
  let range2;
  let irange2;
  let value1;

  if (range) {
    if (transform === undefined) transform = identity;
    if (typeof transform !== "function") throw new TypeError("transform is not a function");
    if (invert === undefined) invert = transform.invert === undefined ? solver(transform) : transform.invert;
    if (typeof invert !== "function") throw new TypeError("invert is not a function");
    let tmin = +transform(min), tmax = +transform(max);
    if (tmin > tmax) [tmin, tmax] = [tmax, tmin];
    range = html`<input  class=double id=fromSlider type=range min=${isFinite(tmin) ? tmin : null} max=${isFinite(tmax) ? tmax : null} step=${step === undefined || (transform !== identity && transform !== negate) ? "any" : step} name=range oninput=${onrange} disabled=${disabled}>`;
    range2 = html`<input  class=double type=range min=${isFinite(tmin) ? tmin : null} max=${isFinite(tmax) ? tmax : null} step=${step === undefined || (transform !== identity && transform !== negate) ? "any" : step} name=range2 oninput=${onrange} disabled=${disabled}>`;


    irange = transform === identity ? range : html`<input type=range min=${min} max=${max} step=${step === undefined ? "any" : step} name=range disabled=${disabled}>`;
    irange2 = transform === identity ? range2 : html`<input type=range min=${min} max=${max} step=${step === undefined ? "any" : step} name=range2 disabled=${disabled}>`;
  } else {
    range = null;
    transform = invert = identity;
  }


// const span = html`<span  class="range_track" id="range_track"></span>`
  const dRange = " double-range"
  const form = html`<form class=__ns__ style=${maybeWidth(width)}>
   <div class=__ns__-input${dRange}>  <div style="font-size:12px"><p>${label}</p></div> <div>
   ${number} - ${numberUpper} </div> <div> ${range}${range2} </div>
    </div>
  </form>`;
  form.addEventListener("submit", preventDefault);
  // If range, use an untransformed range to round to the nearest valid value.
  function coerce(v, slidertype) {
    if (slidertype === 'from'){
    if (!irange) return +v;
    v = Math.max(min, Math.min(numberUpper.valueAsNumber, v));
    if (!isFinite(v)) return v;
    irange.valueAsNumber = v;
    return irange.valueAsNumber;
  } else {
    if (!irange2) return +v;
    v = Math.max(number.valueAsNumber, Math.min(max, v));
    if (!isFinite(v)) return v;
    irange2.valueAsNumber = v;
    return irange2.valueAsNumber;
  }
  }




  function onrange(event) {
 if (event.target.id ==='fromSlider'){
    const v = coerce(invert(range.valueAsNumber),'from');
    if (isFinite(v)) {
      number.valueAsNumber = Math.max(min, Math.min(numberUpper.valueAsNumber, v));
      if (validate(number)) {
        value = [number.valueAsNumber, numberUpper.valueAsNumber]
        number.value = format(value[0])
        return;
      }
    }
    if (event) event.stopPropagation();
  }
  else {
        const v = coerce(invert(range2.valueAsNumber),'to');

if (isFinite(v)) {
      numberUpper.valueAsNumber = Math.max(number.valueAsNumber, Math.min(max, v));
      if (validate(numberUpper)) {
        value = [number.valueAsNumber,numberUpper.valueAsNumber];
        numberUpper.value = format(value[1]);
        return;
      }
    }
     if (event) event.stopPropagation();
  }
    }
  
  function onnumber(event) {        
    if (event.target.id ==='numberLower'){
    const v = coerce(number.valueAsNumber,'from');
    if (isFinite(v)) {
      if (range) range.valueAsNumber = transform(v);
      if (validate(number)) {
        value = [v, numberUpper.valueAsNumber];
        // adjust input box size
        numLenLower = String(Math.max(v.toString().length*10 +10, 22))+'px'
        number.style['width'] = numLenLower
        return;
      }
    }
    if (event) event.stopPropagation();
  } else {
    const v = coerce(numberUpper.valueAsNumber,'to');
    if (isFinite(v)) {
      if (range2) range2.valueAsNumber = transform(v);
      if (validate(numberUpper)) {
        value = [number.valueAsNumber,v];
        // adjust input box size
        numLenUpper = String(Math.max(v.toString().length*10+10, 22))+'px'
        numberUpper.style['width'] = numLenUpper
        return;
      }
    }
    if (event) event.stopPropagation();
  }
  }


  Object.defineProperty(form, "value", {
    get() {
      return value;
    },
    set(v) {
    let v1;
    let v2;
    v1 = coerce(v[0],'from');
    v2 = coerce(v[1],'to');
      if (isFinite(v1) && isFinite(v2)) {
        number.valueAsNumber = v1;
        numberUpper.valueAsNumber = v2;

        if (range) range.valueAsNumber = transform(v1);
        if (range2) range2.valueAsNumber = transform(v2);
        if (validate(number) && validate(numberUpper)) {
          value = [v1,v2]
          number.value = format(v1);
          numberUpper.value = format(v2);

        }
      }
    }
  });
  if (initialValue === undefined && irange) initialValue = [min,max]
  numberUpper.valueAsNumber = max;
  number.valueAsNumber = min;

  // irange.valueAsNumber/2; // (min + max) / 2
  //if (initialValue === undefined && irange2) initialValue = irange2.valueAsNumber; // (min + max) / 2

  if (initialValue !== undefined) form.value = initialValue; // invoke setter
  //if (initialValue !== undefined) form.value = initialValue; // invoke setter

  return form;
}

function negate(x) {
  return -x;
}

function square(x) {
  return x * x;
}

function solver(f) {
  if (f === identity || f === negate) return f;
  if (f === Math.sqrt) return square;
  if (f === Math.log) return Math.exp;
  if (f === Math.exp) return Math.log;
  return x => solve(f, x, x);
}

function solve(f, y, x) {
  let steps = 100, delta, f0, f1;
  x = x === undefined ? 0 : +x;
  y = +y;
  do {
    f0 = f(x);
    f1 = f(x + epsilon);
    if (f0 === f1) f1 = f0 + epsilon;
    x -= delta = (-1 * epsilon * (f0 - y)) / (f0 - f1);
  } while (steps-- > 0 && Math.abs(delta) > epsilon);
  return steps < 0 ? NaN : x;
}


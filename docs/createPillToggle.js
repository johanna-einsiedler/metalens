export function pillToggle(values, {
  label = "",
  value: initialValue,
  format = (v) => v,
  disabled = false
} = {}) {
  let currentValue = initialValue !== undefined ? [...initialValue] : [...values];

  const buttonElements = values.map((val, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pill-toggle-btn";
    btn.setAttribute("data-selected", String(currentValue.includes(val)));
    btn.textContent = format(val);
    if (disabled) btn.disabled = true;

    btn.addEventListener("click", () => {
      if (disabled) return;
      const selected = btn.getAttribute("data-selected") === "true";
      btn.setAttribute("data-selected", String(!selected));
      currentValue = [];
      for (let i = 0; i < buttonElements.length; i++) {
        if (buttonElements[i].getAttribute("data-selected") === "true") {
          currentValue.push(values[i]);
        }
      }
      root.dispatchEvent(new Event("input", {bubbles: true}));
    });

    return btn;
  });

  const pillGroup = document.createElement("div");
  pillGroup.className = "pill-toggle-group";
  for (const btn of buttonElements) pillGroup.appendChild(btn);

  // Build with plain DOM to avoid htl/form-nesting issues
  const labelDiv = document.createElement("div");
  labelDiv.className = "filter-label";
  if (label instanceof Node) {
    labelDiv.appendChild(label);
  } else {
    labelDiv.textContent = label;
  }

  const pillsDiv = document.createElement("div");
  pillsDiv.className = "filter-pills";
  pillsDiv.appendChild(pillGroup);

  const container = document.createElement("div");
  container.className = "filter-container";
  container.appendChild(labelDiv);
  container.appendChild(pillsDiv);

  const root = document.createElement("div");
  root.className = "pill-toggle";
  root.appendChild(container);

  Object.defineProperty(root, "value", {
    get() {
      return currentValue;
    },
    set(v) {
      currentValue = Array.isArray(v) ? [...v] : [];
      for (let i = 0; i < buttonElements.length; i++) {
        const isSelected = currentValue.includes(values[i]);
        buttonElements[i].setAttribute("data-selected", String(isSelected));
      }
    }
  });

  root.updateCount = (valueCounts) => {
    for (let i = 0; i < buttonElements.length; i++) {
      buttonElements[i].textContent = format(values[i]);
    }
  };

  return root;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Request failed");
  }

  return response.json();
}

async function postForm(url, formData) {
  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Request failed");
  }

  return response.json();
}

function showToast(message, kind = "success") {
  const toast = document.getElementById("toast");
  if (!toast) {
    return;
  }

  toast.textContent = message;
  toast.dataset.kind = kind;
  toast.hidden = false;
  requestAnimationFrame(() => toast.classList.add("visible"));

  window.clearTimeout(showToast._timeoutId);
  showToast._timeoutId = window.setTimeout(() => {
    toast.classList.remove("visible");
    window.setTimeout(() => {
      toast.hidden = true;
    }, 220);
  }, 2600);
}

function refreshPage(message) {
  if (message) {
    window.sessionStorage.setItem("dentvoice_toast", message);
  }
  window.location.reload();
}

function restorePendingToast() {
  const pendingToast = window.sessionStorage.getItem("dentvoice_toast");
  if (!pendingToast) {
    return;
  }
  showToast(pendingToast);
  window.sessionStorage.removeItem("dentvoice_toast");
}

function getFilterStorageKey(key) {
  return `dentvoice_saved_filters_${key}`;
}

function serializeForm(form) {
  const formData = new FormData(form);
  const entries = {};
  for (const [key, value] of formData.entries()) {
    entries[key] = value;
  }

  form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    entries[input.name] = input.checked ? input.value : "";
  });
  return entries;
}

function loadSavedFilters(key) {
  try {
    return JSON.parse(window.localStorage.getItem(getFilterStorageKey(key)) || "[]");
  } catch {
    return [];
  }
}

function persistSavedFilters(key, filters) {
  window.localStorage.setItem(getFilterStorageKey(key), JSON.stringify(filters));
}

function applyFilterToForm(form, filter) {
  Object.entries(filter.values).forEach(([name, value]) => {
    const element = form.elements.namedItem(name);
    if (!element) {
      return;
    }
    if (element.type === "checkbox") {
      element.checked = value === element.value;
      return;
    }
    element.value = value;
  });
  form.submit();
}

function renderSavedFilters(form, key) {
  const list = document.querySelector(`[data-saved-filter-list="${key}"]`);
  if (!list) {
    return;
  }

  const filters = loadSavedFilters(key);
  list.innerHTML = "";

  if (!filters.length) {
    list.innerHTML = '<span class="empty-inline">No saved filters yet.</span>';
    return;
  }

  filters.forEach((filter, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "saved-filter-pill";
    button.textContent = filter.name;
    button.addEventListener("click", () => applyFilterToForm(form, filter));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "saved-filter-delete";
    deleteButton.textContent = "x";
    deleteButton.addEventListener("click", () => {
      const nextFilters = loadSavedFilters(key).filter((_, itemIndex) => itemIndex !== index);
      persistSavedFilters(key, nextFilters);
      renderSavedFilters(form, key);
      showToast("Saved filter removed");
    });

    const wrapper = document.createElement("div");
    wrapper.className = "saved-filter-chip";
    wrapper.append(button, deleteButton);
    list.append(wrapper);
  });
}

function collectSelectedIds(selector) {
  return Array.from(document.querySelectorAll(selector))
    .filter((input) => input.checked)
    .map((input) => input.value);
}

function bindBulkForm({ formId, checkboxSelector, endpoint, successMessage }) {
  const form = document.getElementById(formId);
  if (!form) {
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const ids = collectSelectedIds(checkboxSelector);
      if (!ids.length) {
        throw new Error("Select at least one record first.");
      }
      const formData = new FormData(form);
      const hiddenInput = form.querySelector('input[type="hidden"]');
      if (hiddenInput) {
        hiddenInput.value = ids.join(",");
      }
      await postForm(endpoint, formData);
      refreshPage(successMessage);
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

function setupOnboardingWizard() {
  const steps = Array.from(document.querySelectorAll("[data-wizard-step]"));
  if (!steps.length) {
    return;
  }

  let index = 0;
  const currentNode = document.getElementById("wizard-current-step");
  const totalNode = document.getElementById("wizard-total-steps");
  const labelNode = document.getElementById("wizard-step-label");
  const backButton = document.getElementById("wizard-back");
  const nextButton = document.getElementById("wizard-next");

  if (totalNode) {
    totalNode.textContent = String(steps.length);
  }

  function render() {
    steps.forEach((step, stepIndex) => {
      step.hidden = stepIndex !== index;
      step.classList.toggle("wizard-step-active", stepIndex === index);
    });
    if (currentNode) {
      currentNode.textContent = String(index + 1);
    }
    if (labelNode) {
      labelNode.textContent = steps[index]?.dataset.stepLabel || `Step ${index + 1}`;
    }
    if (backButton) {
      backButton.disabled = index === 0;
    }
    if (nextButton) {
      nextButton.disabled = index === steps.length - 1;
    }
  }

  backButton?.addEventListener("click", () => {
    if (index > 0) {
      index -= 1;
      render();
    }
  });

  nextButton?.addEventListener("click", () => {
    if (index < steps.length - 1) {
      index += 1;
      render();
    }
  });

  render();
}

function setupSavedFilters() {
  document.querySelectorAll("form[data-save-filters]").forEach((form) => {
    const key = form.dataset.saveFilters;
    renderSavedFilters(form, key);
  });

  document.querySelectorAll(".save-filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.saveFilterTarget;
      const form = document.querySelector(`form[data-save-filters="${key}"]`);
      if (!form) {
        return;
      }

      const filters = loadSavedFilters(key);
      const name = window.prompt("Name this filter view:");
      if (!name) {
        return;
      }

      filters.push({ name, values: serializeForm(form) });
      persistSavedFilters(key, filters);
      renderSavedFilters(form, key);
      showToast("Filter saved");
    });
  });

  document.querySelectorAll(".clear-filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.clearFilterTarget;
      window.localStorage.removeItem(getFilterStorageKey(key));
      const form = document.querySelector(`form[data-save-filters="${key}"]`);
      if (form) {
        renderSavedFilters(form, key);
      }
      showToast("Saved filters cleared");
    });
  });
}

restorePendingToast();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/service-worker.js").catch(() => {});
  });
}

document.getElementById("seed-demo")?.addEventListener("click", async () => {
  try {
    await postJson("/api/demo/seed", {});
    refreshPage("Demo data loaded");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("simulate-call")?.addEventListener("click", async () => {
  try {
    const slotsResponse = await fetch("/api/available-slots");
    const slotData = await slotsResponse.json();
    const fallbackSlot = slotData.slots?.[0];
    await postJson("/api/simulate-call", {
      caller_number: "+919876543210",
      patient_name: "Demo Patient",
      transcript: "Hello, I want to book an appointment for teeth cleaning this Friday evening",
      preferred_date: fallbackSlot?.date,
      preferred_time: fallbackSlot?.time,
      reason_for_visit: "Teeth cleaning",
    });
    refreshPage("Simulated call added");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("contact-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    const formData = new FormData(form);
    const message = formData.get("message") || "";
    const preferredPlan = formData.get("preferred_plan") || "Starter";
    const paymentMethod = formData.get("payment_method") || "UPI";
    formData.set(
      "message",
      `${message}\nPreferred plan: ${preferredPlan}\nPreferred payment method: ${paymentMethod}`,
    );
    await postForm("/api/contact-request", formData);
    form.reset();
    showToast("Demo request submitted");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("trial-signup-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    const formData = new FormData(form);
    const result = await postForm("/api/trial-signup", formData);
    window.sessionStorage.setItem(
      "dentvoice_toast",
      `Workspace created. Admin login: ${result.admin_username} / ${result.password}`,
    );
    window.location.href = result.redirect_url || "/setup";
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("roi-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const missedCalls = Number(form.missed_calls.value || 0);
  const recoveryRate = Number(form.recovery_rate.value || 0) / 100;
  const bookingValue = Number(form.booking_value.value || 0);
  const recoveredBookings = Math.round(missedCalls * recoveryRate);
  const recoveredRevenue = recoveredBookings * bookingValue;
  const profitAfterSoftware = recoveredRevenue - 4999;
  const target = document.getElementById("roi-result");
  if (!target) {
    return;
  }
  target.innerHTML = `
    <strong>Estimated recovered revenue: ₹${recoveredRevenue.toLocaleString("en-IN")}</strong>
    <p class="message-paragraph">If you recover about ${recoveredBookings} bookings from ${missedCalls} missed calls, DentVoice can create roughly ₹${recoveredRevenue.toLocaleString("en-IN")} in value.</p>
    <p class="message-paragraph">After a ₹4,999 monthly subscription, that leaves an estimated ₹${profitAfterSoftware.toLocaleString("en-IN")} in recovered upside.</p>
  `;
});

document.querySelectorAll(".contact-request-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/contact-requests/${form.dataset.requestId}/update`, formData);
      refreshPage("Demo request updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".faq-create-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm("/api/faqs", formData);
      refreshPage("FAQ added");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".faq-edit-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/faqs/${form.dataset.faqId}/update`, formData);
      refreshPage("FAQ updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".faq-delete").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("Delete this FAQ?")) {
      return;
    }
    try {
      await postForm(`/api/faqs/${button.dataset.faqId}/delete`, new FormData());
      refreshPage("FAQ deleted");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.getElementById("appointment-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/admin/appointments", formData);
    refreshPage("Appointment saved");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("clinic-switch-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/clinic/switch", formData);
    refreshPage("Clinic switched");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("clinic-create-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/clinics", formData);
    refreshPage("Clinic workspace created");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("task-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/receptionist-tasks", formData);
    refreshPage("Receptionist task created");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".task-edit-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/receptionist-tasks/${form.dataset.taskId}/update`, formData);
      refreshPage("Receptionist task updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".missed-lead-task-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/missed-leads/${form.dataset.callId}/task`, formData);
      refreshPage("Recovery task created");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".reminder-create-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm("/api/reminders", formData);
      refreshPage("Reminder queued");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".reminder-update-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/reminders/${form.dataset.reminderId}/update`, formData);
      refreshPage("Reminder updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.getElementById("settings-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    await postJson("/api/settings", {
      clinic_name: form.clinic_name.value,
      clinic_timings: form.clinic_timings.value,
      clinic_address: form.clinic_address.value,
      brand_tagline: form.brand_tagline.value,
      accent_color: form.accent_color.value,
      logo_text: form.logo_text.value,
      business_type: form.business_type.value,
      avg_booking_value: Number(form.avg_booking_value.value || 0),
      white_label_enabled: form.white_label_enabled?.checked || false,
      white_label_name: form.white_label_name?.value || "",
      reseller_code: form.reseller_code?.value || "",
      working_days: form.working_days.value,
      working_hours: form.working_hours.value,
      auto_callback_enabled: form.auto_callback_enabled.checked,
    });
    refreshPage("Clinic settings updated");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("slot-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    await postJson("/api/slots", {
      date: form.date.value,
      time: form.time.value,
    });
    refreshPage("Slot added");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("blocked-time-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/calendar/blocked-times", formData);
    refreshPage("Blocked time saved");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("calendar-resource-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/calendar/resources", formData);
    refreshPage("Scheduling resource added");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("recurring-rule-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/calendar/recurring-rules", formData);
    refreshPage("Recurring rule added");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".slot-delete").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await postForm(`/api/slots/${button.dataset.slotId}/delete`, new FormData());
      refreshPage("Slot removed");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".template-apply-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm("/api/templates/apply", formData);
      refreshPage("Industry template applied");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".appointment-edit-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/appointments/${form.dataset.appointmentId}/update`, formData);
      refreshPage("Appointment updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".appointment-delete").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("Delete this appointment?")) {
      return;
    }
    try {
      await postForm(`/api/appointments/${button.dataset.appointmentId}/delete`, new FormData());
      refreshPage("Appointment deleted");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".call-score-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/calls/${form.dataset.callId}/update`, formData);
      refreshPage("Call record updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.getElementById("team-user-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/team/users", formData);
    refreshPage("Team user created");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".team-user-edit-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/team/users/${form.dataset.userId}/update`, formData);
      refreshPage("Team user updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".team-user-toggle").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      const formData = new FormData();
      formData.set("is_active", button.dataset.nextActive);
      await postForm(`/api/team/users/${button.dataset.userId}/toggle`, formData);
      refreshPage("User access updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.getElementById("password-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/password/change", formData);
    refreshPage("Password updated");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".notification-read").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await postForm(`/api/notifications/${button.dataset.notificationId}/read`, new FormData());
      refreshPage("Notification marked as read");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.getElementById("announcement-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/announcements", formData);
    refreshPage("Announcement posted");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("lead-auto-assign")?.addEventListener("click", async () => {
  try {
    const ids = collectSelectedIds(".lead-select-checkbox");
    if (!ids.length) {
      throw new Error("Select at least one lead first.");
    }
    const formData = new FormData();
    formData.set("request_ids", ids.join(","));
    await postForm("/api/contact-requests/auto-assign", formData);
    refreshPage("Selected leads auto-assigned");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("referral-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/referrals", formData);
    refreshPage("Referral added");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("onboarding-email-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/onboarding-emails", formData);
    refreshPage("Onboarding email queued");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".onboarding-step-complete").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await postForm(`/api/onboarding/steps/${button.dataset.stepKey}`, new FormData());
      refreshPage("Setup progress updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.getElementById("onboarding-preset-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/onboarding/preset", formData);
    refreshPage("Industry preset loaded");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("automation-rule-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/automation-rules", formData);
    refreshPage("Automation rule created");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".comment-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      formData.set("entity_type", form.dataset.entityType);
      formData.set("entity_id", form.dataset.entityId);
      await postForm("/api/comments", formData);
      refreshPage("Comment added");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

let draggedLeadId = null;
document.querySelectorAll(".draggable-lead").forEach((card) => {
  card.addEventListener("dragstart", () => {
    draggedLeadId = card.dataset.requestId;
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => {
    draggedLeadId = null;
    card.classList.remove("dragging");
  });
});

document.querySelectorAll(".lead-stage-dropzone").forEach((column) => {
  column.addEventListener("dragover", (event) => {
    event.preventDefault();
    column.classList.add("drop-active");
  });
  column.addEventListener("dragleave", () => column.classList.remove("drop-active"));
  column.addEventListener("drop", async (event) => {
    event.preventDefault();
    column.classList.remove("drop-active");
    if (!draggedLeadId) {
      return;
    }
    try {
      const formData = new FormData();
      formData.set("status", column.dataset.stage);
      await postForm(`/api/contact-requests/${draggedLeadId}/stage`, formData);
      refreshPage("Lead stage updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

let draggedAppointmentId = null;
document.querySelectorAll(".draggable-appointment").forEach((item) => {
  item.addEventListener("dragstart", () => {
    draggedAppointmentId = item.dataset.appointmentId;
    item.classList.add("dragging");
  });
  item.addEventListener("dragend", () => {
    draggedAppointmentId = null;
    item.classList.remove("dragging");
  });
});

document.querySelectorAll(".calendar-dropzone").forEach((zone) => {
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("drop-active");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drop-active"));
  zone.addEventListener("drop", async (event) => {
    event.preventDefault();
    zone.classList.remove("drop-active");
    if (!draggedAppointmentId) {
      return;
    }
    try {
      const formData = new FormData();
      formData.set("preferred_date", zone.dataset.date);
      await postForm(`/api/calendar/appointments/${draggedAppointmentId}/move`, formData);
      refreshPage("Appointment moved");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

setupSavedFilters();
setupOnboardingWizard();
bindBulkForm({
  formId: "lead-bulk-form",
  checkboxSelector: ".lead-select-checkbox",
  endpoint: "/api/contact-requests/bulk-update",
  successMessage: "Lead bulk update applied",
});
bindBulkForm({
  formId: "task-bulk-form",
  checkboxSelector: ".task-select-checkbox",
  endpoint: "/api/receptionist-tasks/bulk-update",
  successMessage: "Task bulk update applied",
});
bindBulkForm({
  formId: "reminder-bulk-form",
  checkboxSelector: ".reminder-select-checkbox",
  endpoint: "/api/reminders/bulk-update",
  successMessage: "Reminder bulk update applied",
});

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

function refreshPage() {
  window.location.reload();
}

document.getElementById("seed-demo")?.addEventListener("click", async () => {
  try {
    await postJson("/api/demo/seed", {});
    refreshPage();
  } catch (error) {
    alert(error.message);
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
    refreshPage();
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("appointment-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/admin/appointments", formData);
    refreshPage();
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("settings-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    await postJson("/api/settings", {
      clinic_name: form.clinic_name.value,
      clinic_timings: form.clinic_timings.value,
      clinic_address: form.clinic_address.value,
    });
    refreshPage();
  } catch (error) {
    alert(error.message);
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
    refreshPage();
  } catch (error) {
    alert(error.message);
  }
});

document.querySelectorAll(".slot-delete").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await postForm(`/api/slots/${button.dataset.slotId}/delete`, new FormData());
      refreshPage();
    } catch (error) {
      alert(error.message);
    }
  });
});

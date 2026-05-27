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

async function refreshPage() {
  window.location.reload();
}

document.getElementById("seed-demo")?.addEventListener("click", async () => {
  try {
    await postJson("/api/demo/seed", {});
    await refreshPage();
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("simulate-call")?.addEventListener("click", async () => {
  try {
    await postJson("/api/simulate-call", {
      caller_number: "+919876543210",
      patient_name: "Demo Patient",
      transcript: "Hello, I want to book an appointment for teeth cleaning this Friday evening",
      preferred_date: "2026-05-16",
      preferred_time: "6:00 PM",
      reason_for_visit: "Teeth cleaning",
    });
    await refreshPage();
  } catch (error) {
    alert(error.message);
  }
});

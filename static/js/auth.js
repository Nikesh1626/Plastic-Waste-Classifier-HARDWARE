// auth.js — Supabase Google-only authentication helpers

let supabaseClient = null;
let currentSession = null;

function authElements() {
  return {
    signInBtn: document.getElementById("authSignInBtn"),
    profileBtn: document.getElementById("profileBtn"),
    profileMenu: document.getElementById("profileMenu"),
    profileEmail: document.getElementById("profileEmail"),
    profileSignOutBtn: document.getElementById("profileSignOutBtn"),
    authNavArea: document.getElementById("authNavArea"),
    modal: document.getElementById("authModal"),
    modalCloseBtn: document.getElementById("authModalCloseBtn"),
    googleSignInBtn: document.getElementById("googleSignInBtn"),
    modalMessage: document.getElementById("authModalMessage"),
  };
}

function setAuthButtonState() {
  const { signInBtn, profileBtn, profileMenu, profileEmail } = authElements();
  if (!signInBtn || !profileBtn || !profileMenu || !profileEmail) return;

  const user = currentSession?.user;
  if (user) {
    profileEmail.textContent = user.email || "Signed in";
    signInBtn.classList.add("hidden");
    profileBtn.classList.remove("hidden");
    profileBtn.classList.add("inline-flex");
  } else {
    signInBtn.classList.remove("hidden");
    profileBtn.classList.add("hidden");
    profileBtn.classList.remove("inline-flex");
    profileMenu.classList.add("hidden");
    profileBtn.setAttribute("aria-expanded", "false");
  }
}

function openProfileMenu() {
  const { profileMenu, profileBtn } = authElements();
  if (!profileMenu || !profileBtn) return;
  profileMenu.classList.remove("hidden");
  profileBtn.setAttribute("aria-expanded", "true");
}

function closeProfileMenu() {
  const { profileMenu, profileBtn } = authElements();
  if (!profileMenu || !profileBtn) return;
  profileMenu.classList.add("hidden");
  profileBtn.setAttribute("aria-expanded", "false");
}

function toggleProfileMenu() {
  const { profileMenu } = authElements();
  if (!profileMenu) return;
  if (profileMenu.classList.contains("hidden")) {
    openProfileMenu();
  } else {
    closeProfileMenu();
  }
}

function showAuthModal() {
  const { modal } = authElements();
  if (!modal) return;
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  modal.setAttribute("aria-hidden", "false");
}

window.showAuthModal = showAuthModal;

function hideAuthModal() {
  const { modal } = authElements();
  if (!modal) return;
  modal.classList.add("hidden");
  modal.classList.remove("flex");
  modal.setAttribute("aria-hidden", "true");
}

window.hideAuthModal = hideAuthModal;

async function signInWithGoogle() {
  const { googleSignInBtn, modalMessage } = authElements();
  if (!supabaseClient) {
    if (modalMessage) {
      modalMessage.textContent =
        "Google sign-in is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY in the server .env file.";
    }
    return;
  }

  if (googleSignInBtn) googleSignInBtn.disabled = true;
  try {
    const { error } = await supabaseClient.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: window.location.origin + window.location.pathname,
        queryParams: {
          access_type: "offline",
          prompt: "consent",
        },
      },
    });
    if (error) throw error;
  } catch (error) {
    console.error("Google sign-in failed:", error);
    if (modalMessage) {
      modalMessage.textContent =
        error?.message || "Google sign-in could not be started. Please try again.";
    }
  } finally {
    if (googleSignInBtn) googleSignInBtn.disabled = false;
  }
}

async function signOutSupabase() {
  if (!supabaseClient) return;
  await supabaseClient.auth.signOut();
  currentSession = null;
  setAuthButtonState();
}

window.authRequireUser = async function authRequireUser() {
  if (currentSession?.access_token) {
    return true;
  }
  showAuthModal();
  return false;
};

window.getAuthToken = function getAuthToken() {
  return currentSession?.access_token || "";
};

async function initAuth() {
  const supabaseConfig = document.getElementById("supabaseConfig");
  const supabaseUrl = (supabaseConfig?.dataset?.url || "").trim();
  const supabaseAnonKey = (supabaseConfig?.dataset?.anonKey || "").trim();
  const {
    signInBtn,
    profileBtn,
    profileSignOutBtn,
    authNavArea,
    modalCloseBtn,
    googleSignInBtn,
    modalMessage,
  } = authElements();

  if (!supabaseUrl || !supabaseAnonKey || !window.supabase?.createClient) {
    if (modalMessage) {
      modalMessage.textContent = !window.supabase?.createClient
        ? "The Supabase client could not load. Check your internet connection and reload the page."
        : "Google sign-in is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY in the server .env file.";
    }
    if (signInBtn) {
      signInBtn.disabled = true;
      signInBtn.textContent = "Auth unavailable";
      signInBtn.title = "Set SUPABASE_URL and SUPABASE_ANON_KEY in .env";
    }
    if (googleSignInBtn) {
      googleSignInBtn.disabled = true;
      googleSignInBtn.textContent = "Google sign-in unavailable";
    }
    return;
  }

  supabaseClient = window.supabase.createClient(supabaseUrl, supabaseAnonKey);

  const { data } = await supabaseClient.auth.getSession();
  currentSession = data.session;
  setAuthButtonState();

  if (signInBtn) {
    signInBtn.addEventListener("click", async () => {
      showAuthModal();
    });
  }

  if (profileBtn) {
    profileBtn.addEventListener("click", () => {
      toggleProfileMenu();
    });
  }

  if (profileSignOutBtn) {
    profileSignOutBtn.addEventListener("click", async () => {
      closeProfileMenu();
      await signOutSupabase();
    });
  }

  if (authNavArea) {
    document.addEventListener("click", (event) => {
      if (!authNavArea.contains(event.target)) {
        closeProfileMenu();
      }
    });
  }

  if (modalCloseBtn) {
    modalCloseBtn.addEventListener("click", hideAuthModal);
  }

  if (googleSignInBtn) {
    googleSignInBtn.addEventListener("click", async () => {
      await signInWithGoogle();
    });
  }

  supabaseClient.auth.onAuthStateChange((_event, session) => {
    currentSession = session;
    setAuthButtonState();
    if (session?.user) {
      hideAuthModal();
      closeProfileMenu();
    }
  });
}

document.addEventListener("DOMContentLoaded", initAuth);

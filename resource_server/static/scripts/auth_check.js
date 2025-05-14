window.dependencyReady = new Promise((resolve) => {
    document.addEventListener('DOMContentLoaded', async () => {
        const navLinks = document.getElementById('nav-links');
        const auth_exp = localStorage.getItem('access_exp');
        const leeway = localStorage.getItem('leeway');

        async function removeAuthDetails() {
            auth = false;
            localStorage.removeItem('login');
            localStorage.removeItem('access_exp');
            localStorage.removeItem('leeway');
        }

        async function loadAuthButtons() {
            const loginButton = document.createElement('button');
            loginButton.innerText = 'Login';
            loginButton.classList.add('btn-primary');
            loginButton.addEventListener('click', () => {
                window.location.href = '/login'
            });

            const signupButton = document.createElement('button');
            signupButton.innerText = 'Sign Up';
            signupButton.classList.add('btn-primary');
            signupButton.addEventListener('click', () => {
                window.location.href = '/signup'
            });

            navLinks.appendChild(loginButton);
            navLinks.appendChild(signupButton);
        }

        if (navLinks) {
            if (!auth_exp || auth_exp === undefined || auth_exp.trim() === '') {
                removeAuthDetails();
                loadAuthButtons()
                return resolve();
            }

            let auth_time = null;
            try {
                auth_time = parseFloat(auth_exp) + parseFloat(leeway)
            }
            catch (error) {
                alert('Your authentication details seem to be invalid. Please login again. If the issue persists, contact support');
                removeAuthDetails()
                return resolve()
            }

            if (auth_time && auth_time > Date.now() / 1000) {
                const img_icon = document.createElement('img');
                img_icon.classList.add('header-icon');
                img_icon.src = "/static/assets/user_icon.png";
                navLinks.appendChild(img_icon);

                const logoutButton = document.createElement('button');
                logoutButton.classList.add('btn-primary');
                logoutButton.innerText = 'Logout';
                logoutButton.addEventListener('click', async () => {
                    try {
                        const response = await fetch('http://192.168.0.104:8000/purge-family', {
                            method: 'GET',
                            credentials: 'include'
                        });

                        if (!response.ok) {
                            throw new Error('Failed to logout correctly');
                        }

                        removeAuthDetails();
                        window.location.href = '/';
                        return;
                    }

                    catch (error) {
                        removeAuthDetails();
                        loadAuthButtons();
                        console.error(error);
                    }
                });

                navLinks.appendChild(logoutButton);
            }
            else {
                loadAuthButtons();
            }
        }

        localStorage.setItem('login', true);
        resolve();
    })
})
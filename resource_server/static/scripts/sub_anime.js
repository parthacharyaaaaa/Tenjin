document.addEventListener('DOMContentLoaded', () => {
    const animeID = window.location.pathname.split("/")[3];
    window.dependencyReady?.then(async () => {
        let members = document.getElementById('members').innerText;
        let showChange = false;
        if(members && members !== 'N/A'){
            members = members.replace(',', '').replace('.','');
            if (!isNaN(members)){
                members = parseInt(members);
                showChange = true;
            }
        }

        const subButton = document.getElementById("anime-sub-btn");
        if (!localStorage.getItem('login')) {
            subButton.innerText = 'Login to subscribe to this anime!';
            subButton.addEventListener('click', () => {
                window.location.href = '/login';
            });
            return;
        }
        let isSubbed = (subButton.value && subButton.value.trim() === 'false') ? false : true
        subButton.innerText = !isSubbed ? 'Subscribe to this anime' : 'Unsubscribe from this anime'

        subButton.addEventListener('click', async () => {
            try {
                const response = await fetch(`/animes/${animeID}/${isSubbed ? 'unsubscribe' : 'subscribe'}`, {
                    method: "PATCH",
                    credentials: 'include'
                });

                if (!response.ok) {
                    throw new Error(`Failed to ${isSubbed ? 'unsubscribe from' : 'subscribe to'} this anime`);
                }
                

                if (isSubbed) {
                    subButton.innerText = 'Subscribe to this anime';
                    members--;
                    if (showChange) {
                        document.getElementById('members').innerText = members;
                    }
                } else {
                    subButton.innerText = 'Unsubscribe from this anime';
                    if (showChange) {
                        members++;
                        document.getElementById('members').innerText = members === 999 ? '1K' : members;
                    }
                }
                isSubbed = !isSubbed;
            }
            catch (error) {
                console.error(error);
            }
        });
    });
});
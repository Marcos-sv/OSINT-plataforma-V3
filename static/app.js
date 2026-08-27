const tabs =
    document.querySelectorAll('.tab');

const panels =
    document.querySelectorAll('.panel');

const statusEl =
    document.getElementById('status');

const outputEl =
    document.getElementById('output');

const downloadsEl =
    document.getElementById('downloads');


tabs.forEach((tab) => {

    tab.addEventListener('click', () => {

        tabs.forEach((item) => {
            item.classList.remove('active');
        });

        panels.forEach((panel) => {
            panel.classList.remove('active');
        });

        tab.classList.add('active');

        document
            .getElementById(tab.dataset.tab)
            .classList.add('active');

    });

});


function setLoading(message) {

    statusEl.textContent = message;

    outputEl.textContent = '';

    downloadsEl.innerHTML = '';

}


function showResponse(data) {

    if (!data.ok) {

        statusEl.textContent =
            'Erro na consulta.';

        outputEl.textContent =
            data.erro || 'Erro desconhecido.';

        return;

    }

    statusEl.textContent =
        `Relatório gerado: ${data.arquivo}`;

    outputEl.textContent =
        JSON.stringify(
            data.relatorio,
            null,
            2
        );

    const links = [
        `<a href="${data.download}">
            Baixar JSON
        </a>`
    ];

    if (data.integrado) {

        links.push(
            `<a href="${data.integrado}">
                Baixar relatório integrado
            </a>`
        );

    }

    downloadsEl.innerHTML =
        links.join('');

}


async function postJson(url, payload) {

    const response = await fetch(
        url,
        {
            method: 'POST',

            headers: {
                'Content-Type':
                    'application/json'
            },

            body: JSON.stringify(payload),
        }
    );

    const data =
        await response.json();

    showResponse(data);

}


document
    .getElementById('osint-form')
    .addEventListener(
        'submit',
        async (event) => {

            event.preventDefault();

            setLoading(
                'Executando osint.py...'
            );

            try {

                await postJson(
                    '/api/osint',
                    {
                        nome:
                            document
                                .getElementById('nome')
                                .value,

                        cpf:
                            document
                                .getElementById('cpf')
                                .value,
                    }
                );

            } catch (error) {

                showResponse({
                    ok: false,
                    erro: error.message
                });

            }

        }
    );


document
    .getElementById('maigret-form')
    .addEventListener(
        'submit',
        async (event) => {

            event.preventDefault();

            setLoading(
                'Executando Maigret...'
            );

            try {

                await postJson(
                    '/api/maigret',
                    {
                        username:
                            document
                                .getElementById('username')
                                .value,
                    }
                );

            } catch (error) {

                showResponse({
                    ok: false,
                    erro: error.message
                });

            }

        }
    );
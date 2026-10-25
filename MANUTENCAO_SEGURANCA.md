# Fluxo de manutenção de segurança e desempenho

Este fluxo organiza a triagem e a correção dos itens levantados em
`analise_seguranca_performance.md` (relatório externo, gerado em 2026-10-24).
O relatório é uma entrada para investigação, não uma confirmação de bugs: valide
código, contexto e configuração deste repositório antes de alterar o driver.
Há divergência entre o resumo do metadata (9 críticas/14 altas) e o resumo do
corpo do relatório (11 críticas/16 altas), então não use esses totais como
métrica de progresso.

## Validação e melhorias aplicadas

Na revisão da árvore atual foram confirmados e corrigidos:

- `rwnx_start_xmit`: o caminho de erro de `rwnx_prep_tx` liberava o SKB e
  retornava `NETDEV_TX_BUSY`; agora informa que o pacote foi consumido.
- Parser AMSDU RX: `sublen` podia ser menor que 6 e sofrer underflow; agora
  valida o tamanho mínimo, o comprimento do subframe e o padding antes de
  alocar/copiar, e libera o sub-SKB se não houver VIF.
- Probe USB: em falha parcial das alocações `vmalloc`, liberava o objeto errado
  e repetia a liberação; agora libera os buffers individualmente e o objeto.
- Envio de comando: verifica consistência entre `len` e `param_len` e que o
  frame cabe em `CMD_BUF_MAX` antes de preencher o buffer compartilhado.
- Alocação do buffer TCP ACK: retorna `NULL` imediatamente em falha de
  `kzalloc`, evitando `memset(NULL, ...)`; o chamador também verifica a falha e
  transmite pelo caminho TX normal sem filtro de ACK.
- Reordenação RX: valida TID contra as 8 entradas de `preorder_ctrl`; TIDs fora
  do intervalo seguem sem reordenação, evitando índice fora dos limites.
- Confirmações do firmware: os chamadores passam a capacidade real dos buffers;
  o tamanho do protocolo é limitado antes de callbacks, e respostas maiores
  que o destino são rejeitadas antes da cópia, completam o comando com
  `-EMSGSIZE` e propagam esse erro ao chamador.
- Mensagens RX do firmware: valida índices de VIF/STA antes de consultar as
  tabelas em notificações P2P, RSSI, perda de pacotes, conexão/desconexão e
  mesh; também rejeita índices de canal negativos ou fora do array de survey.
- Teardown USB: para thread/tasklet TX antes da liberação, cancela o trabalho
  RX e todos os URBs TX do pool, e drena `tx_post_list` liberando SKBs/buffers
  pendentes antes de destruir o pool. Os trabalhos RX agora são inicializados
  antes das alocações para que a limpeza de falha parcial possa cancelá-los com
  segurança.

Validação executada em 2026-10-25: `make -C drivers/aic8800` concluiu com
sucesso contra `/lib/modules/7.3.0-9-generic/build` (x86_64), inclusive após a
proteção de TID e o limite de tamanho das confirmações, gerando os três módulos.
As 8 verificações em `tests/driver-safety` e os 8 testes do instalador passaram.
A compilação emitiu warnings de fallthrough em outros caminhos e não gerou BTF
por falta de `vmlinux`; não houve erro de compilação. Hardware USB e SDIO não
foi exercitado, portanto o resultado confirma compilação, não operação em
runtime.

Nas rodadas seguintes foram incluídas validações dos índices usados por
mensagens RX de firmware e ajustes no teardown USB. As 11 verificações de
segurança passaram e `make -C drivers/aic8800` compilou novamente os três
módulos no mesmo kernel. A compilação manteve warnings de fallthrough em
caminhos existentes e não gerou BTF por falta de `vmlinux`.

## Triagem dos itens ainda abertos (2026-10-25)

Uma leitura estática inicial dos caminhos abaixo atualiza a prioridade da
próxima rodada. Isto não substitui a validação dos contratos de transporte nem
os testes de runtime.

1. **Teardown de fragmentação RX — correção aplicada; validação runtime pendente.**
   Em `rwnx_main.c`, a limpeza de `defrag_list` mantém `defrag_lock` enquanto
   chama `timer_delete_sync()`/`del_timer_sync()` e libera cada entrada. O
   callback `defrag_timeout_cb()` em `rwnx_rx.c` também adquire `defrag_lock`.
   Se o callback estiver executando e esperando pelo lock, o teardown espera o
   callback enquanto mantém esse mesmo lock. A correção desanexa as entradas
   sob lock, espera os timers fora do lock e coordena o ownership com callback
   e fragmento final. Falta exercitar desconexão/teardown concorrente em
   hardware ou com lockdep/KASAN.

2. **Comprimento e ID das mensagens RX — limites básicos corrigidos; validação funcional pendente.**
   `aicwf_txrxif.c` valida o cabeçalho mínimo, o tamanho total do frame contra
   os bytes restantes, o tamanho mínimo de confirmação TX e a consistência do
   `param_len` de respostas de comando antes do despacho. A busca do handler em
   `rwnx_msg_rx.c` também confere o índice da task e da mensagem contra o
   tamanho da tabela. Ainda falta auditar os campos que cada callback lê e
   exercitar entradas malformadas em hardware/sob sanitizadores; classificar
   como correção parcial com risco residual. Nesta rodada, as notificações de
   panic/assert agora validam o tamanho total e o comprimento interno antes de
   copiar dados; frequência de canal desconhecida retorna `-ENOENT` em vez de
   acionar `BUG_ON`. Os 19 testes estruturais passaram e `rwnx_msg_rx.o`
   compilou no kernel `7.3.0-9-generic`. Falta exercitar mensagens malformadas
   em runtime/sanitizadores e continuar a auditoria dos outros callbacks.

3. **Buffer de comando SDIO — corrigido; validação em hardware pendente.**
   `aicwf_set_cmd_tx()` serializa cada mensagem no único `bus->cmd_buf` antes de
   chamar `aicwf_bus_txmsg()`. No SDIO, `aicwf_sdio_bus_txmsg()` publica esse
   mesmo ponteiro em `tx_priv->cmd_buf`, sinaliza o worker e retorna antes da
   transmissão. Além disso, `rwnx_send_msg_sized()` envia mensagens sem
   confirmação diretamente, fora da fila serializada do command manager. Uma
   segunda chamada concorrente pode sobrescrever os bytes enquanto o worker
   ainda os consome. A mensagem original `a2e_msg` é copiada durante a
   serialização; o problema é a propriedade do buffer compartilhado.
   O SDIO agora enfileira cada comando com buffer de propriedade exclusiva;
   o worker transmite e libera cada entrada, e o teardown drena a fila depois
   de parar o worker. A alocação ocorre antes de publicar a entrada e é desfeita
   se o barramento parar ou a alocação falhar. Os objetos `aicwf_sdio.o`,
   `aicwf_txrxif.o` e `rwnx_cmds.o` compilaram com SDIO e USB habilitados e
   prealloc desabilitado, e a regressão estrutural verifica ownership e flush.
   O hardware SDIO não está disponível; concorrência e teardown ainda precisam
   de validação funcional em dispositivo.

4. **Timers TCP ACK — correção de teardown aplicada; validação runtime pendente.**
   `tcp_ack_deinit()` desabilita o gerenciador e agora aguarda cada callback
   (`del_timer_sync()`/`timer_delete_sync()`) antes de adquirir o seqlock e
   liberar a mensagem em espera. A espera fica fora do seqlock, que também é
   usado pelo callback, evitando deadlock. O teardown chama a rotina antes de
   `rwnx_platform_off()` e da destruição do cache TX, para que callback ativo
   termine o envio antes da infraestrutura ser desmontada. A compilação dos
   objetos `rwnx_main.o` e `aicwf_tcp_ack.o` e a regressão estrutural passaram;
   falta validar desconexão concorrente em hardware e com lockdep/KASAN.

### Atualização: teardown de fragmentação RX

O risco descrito no item 1 foi corrigido na árvore de trabalho. O teardown
desanexa a lista sob `defrag_lock` e espera/cancela cada timer depois de soltar
o lock. O callback só libera uma entrada se conseguir removê-la da lista;
fragmento final e teardown removem a entrada antes de cancelar o timer de forma
síncrona e liberar seus recursos. A entrada e o timer são inicializados antes
de publicar a entrada na lista. As alocações feitas dentro da região protegida
usam `GFP_ATOMIC`.

Validação em 2026-10-25: as 13 verificações de `tests/driver-safety` passaram;
`make -C drivers/aic8800` compilou os três módulos para
`7.3.0-9-generic`/x86_64. Permanecem warnings de fallthrough em outros caminhos
e a geração BTF foi ignorada por falta de `vmlinux`. Não houve teste de
desconexão/reconexão nem instrumentação lockdep/KASAN; runtime ainda pendente.

### Atualização: limites do transporte RX e dispatch de mensagens

O parser exige o cabeçalho de transporte completo e confere que o tamanho total
do frame, incluindo alinhamento/overhead, cabe nos bytes disponíveis. Para
respostas de comando, confere também o cabeçalho `ipc_e2a_msg` e se o payload
declarado cabe no frame; para confirmação TX, exige os 8 bytes consumidos pelo
handler. A busca do callback valida ambos os índices antes de acessar a tabela.
As 15 verificações estruturais passaram e `make -C drivers/aic8800` recompilou
com sucesso o módulo alterado no kernel `7.3.0-9-generic`. Não foram injetados
frames malformados em hardware nem usados KASAN/UBSAN; a auditoria dos tamanhos
mínimos lidos por cada callback continua aberta.

Priorizar o teardown dos timers TCP ACK antes de otimizações de performance.
Depois, continuar a auditoria dos campos lidos por callbacks RX e
dos demais itens de memória/cópia do relatório original; as medições atuais de
throughput não cobrem frames malformados ou transporte SDIO.

## 1. Registrar e confirmar cada achado

Para cada item, abra uma issue ou tarefa com: ID do relatório, arquivo e função
(mais confiáveis que números de linha), configuração afetada (USB/SDIO,
firmware, kernel), caminho de entrada, impacto demonstrável, evidência e
responsável. Números de linha mudam e podem não corresponder à árvore atual.

Classifique o estado como **a confirmar**, **confirmado**, **não reproduzido**,
**falso positivo** ou **corrigido**. Inspecione os chamadores, tipos e limites,
caminhos de erro e sincronização. Não aceite a severidade nem o patch proposto
no relatório sem essa revisão. Por exemplo, o retorno `NETDEV_TX_BUSY` exige
confirmar a propriedade do SKB em todos os caminhos; uma substituição mecânica
por `NETDEV_TX_OK` também pode ocultar perda de pacote.

## 2. Ordenar o trabalho

Trate primeiro problemas confirmados que permitam escrita/leitura fora dos
limites, uso após liberação, corrupção de memória ou travamento por entrada
externa. Depois trate corridas e falhas de teardown; em seguida estabilidade e
desempenho. Dentro de cada classe, prefira reproduções alcançáveis sem
privilégios e com impacto maior. P0/P1/P2 no relatório são prioridades
provisórias, não uma classificação de risco concluída.

Agrupe apenas mudanças que compartilhem o mesmo caminho e possam ser revistas
em conjunto. Mantenha cada correção pequena e independente, incluindo validação
de limites, propriedade de buffers, sincronização e limpeza de recursos quando
aplicável. Documente qualquer achado que não for corrigido e a justificativa.

## 3. Corrigir com revisão focada

Cada alteração deve explicar a condição inválida, preservar o contrato da API do
kernel e cobrir todos os caminhos de sucesso e falha. Em particular:

- valide comprimentos antes de subtrair, somar, indexar ou copiar;
- valide índices vindos de firmware/pacotes antes de acessar arrays;
- confira contexto de execução e permissões de alocação antes de dormir;
- defina explicitamente quem possui SKB/URB/buffers em cada retorno;
- cancele e aguarde callbacks antes de liberar estado USB/SDIO;
- não mova chamadas para fora de locks sem reavaliar lifetime e ordenação;
- não use `BUG()` para condições alcançáveis por tráfego ou firmware externo.

Peça revisão de alguém familiarizado com o caminho afetado e com a API de rede,
USB ou SDIO correspondente. Para mudanças de concorrência, inclua a sequência de
lock/unlock e teardown na descrição.

## 4. Validar cada correção

Antes de integrar, registre os comandos e resultados usados. No mínimo, compile
contra os kernels/configurações suportados relevantes. Para correções de
protocolo ou memória, acrescente um caso reproduzível ou teste de regressão
apropriado; para concorrência e teardown, exercite carga, desconexão e
reconexão. Quando disponíveis, use ferramentas de diagnóstico do kernel
(por exemplo, KASAN, lockdep e UBSAN) em ambiente de teste. Não exponha uma
máquina de uso diário a reproduções que possam travar o kernel.

Execute também as verificações estruturais sem hardware com
`python3 -m unittest discover -s tests/driver-safety -p 'test_*.py'`. Elas
protegem os contratos das correções cobertas, mas não substituem compilação nem
testes de runtime.

Valide separadamente USB e SDIO quando o código corrigido for compartilhado ou
quando ambos implementarem o mesmo contrato. Registre kernel, arquitetura,
configuração, chipset/firmware e resultado. Uma compilação bem-sucedida não
confirma segurança funcional.

## 5. Encerrar e acompanhar

Marque como corrigido somente quando a mudança estiver integrada, a regressão
for coberta (ou a limitação estiver documentada) e as validações estiverem
registradas. Atualize o relatório/backlog com commit ou referência da mudança,
resultado e risco residual. Reabra se uma nova reprodução contradisser a
conclusão. Revise achados pendentes a cada ciclo de manutenção e após mudanças
grandes no kernel, transporte ou firmware.

## Critério de pronto por correção

- [ ] Achado confirmado no código e no contexto correto.
- [ ] Impacto e condição de disparo documentados.
- [ ] Correção pequena, revisada e sem mudança de contrato inadvertida.
- [ ] Compilação e validação funcional relevantes registradas.
- [ ] Regressão automatizada ou procedimento reproduzível documentado.
- [ ] Backlog atualizado com evidência e risco residual.

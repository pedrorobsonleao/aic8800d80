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

1. **Teardown de fragmentação RX — risco de deadlock confirmado no código; correção pendente.**
   Em `rwnx_main.c`, a limpeza de `defrag_list` mantém `defrag_lock` enquanto
   chama `timer_delete_sync()`/`del_timer_sync()` e libera cada entrada. O
   callback `defrag_timeout_cb()` em `rwnx_rx.c` também adquire `defrag_lock`.
   Se o callback estiver executando e esperando pelo lock, o teardown espera o
   callback enquanto mantém esse mesmo lock. Corrigir separando a remoção das
   entradas sob lock do cancelamento síncrono e da liberação fora do lock, com
   lifetime protegido contra a corrida entre callback, fragmento final e
   teardown; acrescentar teste/revisão de concorrência antes de integrar.

2. **Comprimento efetivo das mensagens RX — auditoria incompleta, prioridade alta.**
   `rwnx_rx_handle_msg()` indexa a tabela de handlers e chama `msgind`; a
   checagem em `cmd_mgr_msgind()` limita `param_len` ao máximo do protocolo e
   protege o destino de confirmações, mas não recebe o número de bytes
   disponíveis no frame recebido. Os parsers USB/SDIO em `aicwf_txrxif.c`
   copiam `aggr_len + 4` e despacham o cabeçalho como `ipc_e2a_msg`.
   Rastrear o enquadramento até a origem de `pkt_len`, provar que cabeçalho e
   payload estão dentro do bloco recebido antes do despacho, e validar cada
   tamanho mínimo por handler antes de ler `msg->param`. Ainda não há base para
   classificar um acesso fora dos limites como reproduzido; marcar como
   **a confirmar**, com correção/ teste pendentes.

3. **Buffer de comando SDIO — origem da mensagem copiada; serialização pendente.**
   `aicwf_set_cmd_tx()` serializa `a2e_msg` em `bus->cmd_buf` antes de chamar
   `aicwf_bus_txmsg()`, então o `kfree(cmd->a2e_msg)` posterior não prova, por
   si só, uso após liberação da mensagem original. Porém,
   `aicwf_sdio_bus_txmsg()` publica o ponteiro compartilhado em
   `tx_priv->cmd_buf`, sinaliza o worker e retorna sem esperar a transmissão.
   Verificar todas as chamadas concorrentes e a leitura/consumo do worker para
   demonstrar que uma segunda serialização não sobrescreve o buffer antes do
   envio. Estado: **a confirmar**; não aplicar correção até fechar o contrato
   de sincronização.

4. **Timers TCP ACK — teardown requer revisão de sincronização.**
   `tcp_ack_deinit()` desabilita o gerenciador e usa `del_timer()`/`timer_delete()`
   enquanto mantém o seqlock; essas variantes não aguardam necessariamente um
   callback já ativo, que pode usar `ack_m->priv` e transmitir. Verificar a
   ordem de teardown e uso do seqlock no callback antes de trocar por uma
   variante síncrona; uma espera síncrona sob o lock do callback pode deadlock.
   Estado: **a confirmar**.

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

Depois desses itens, continuar a auditoria de comprimentos/cópias e demais
índices de mensagens RX do relatório original. Só então retomar otimizações de
performance; as medições atuais de throughput não cobrem estabilidade de
teardown, frames malformados ou transporte SDIO.

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

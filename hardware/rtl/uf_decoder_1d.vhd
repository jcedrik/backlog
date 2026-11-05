--------------------------------------------------------------------------------
-- uf_decoder_1d: Union-Find decoder for a 1D chain (repetition code), in RTL.
--
-- One growth round per clock cycle. All cluster bookkeeping (segment parity,
-- boundary contact, oddness) is combinational over the current grown-edge
-- vector, so there is no pointer chasing and no memory: just registers and
-- two segmented scans, a prefix and a suffix. Peeling is also combinational
-- and is registered in the cycle where no odd cluster remains.
--
-- With PARALLEL (the default) the scans are Kogge-Stone: ceil(log2 N) levels
-- of logic per growth round instead of a chain through all N detectors, so
-- the clock barely slows down as N grows. PARALLEL => false keeps the
-- original ripple chains, as the baseline for hardware/README.md.
--
-- Latency = number of growth rounds (reported on `rounds`) + 1 cycle to latch
-- the inputs. The golden model is hardware/model/uf1d_model.py and the RTL
-- must match it bit for bit (see hardware/tb/tb_uf_decoder_1d.vhd).
--
-- Layout (N detectors, N+1 data qubits, code distance d = N + 1):
--   edge 0     : left boundary -- D0
--   edge k     : D(k-1) -- D(k)       1 <= k <= N-1
--   edge N     : D(N-1) -- right boundary
--   obs_flip   : predicted flip of data qubit 0 (= correction(0))
--------------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity uf_decoder_1d is
  generic (
    N        : positive := 8;    -- number of detectors
    PARALLEL : boolean  := true  -- Kogge-Stone scans (true) or ripple chains (false)
  );
  port (
    clk        : in  std_logic;
    rst        : in  std_logic;                     -- synchronous, active high
    start      : in  std_logic;                     -- 1-cycle pulse when idle
    syndrome   : in  std_logic_vector(N-1 downto 0);
    busy       : out std_logic;
    done       : out std_logic;                     -- 1-cycle pulse, outputs valid
    correction : out std_logic_vector(N downto 0);  -- one bit per data qubit
    obs_flip   : out std_logic;
    rounds     : out unsigned(7 downto 0)           -- growth rounds used
  );
end entity uf_decoder_1d;

architecture rtl of uf_decoder_1d is

  -- ceil(log2(size)): the number of levels of a Kogge-Stone scan over size elements
  function clog2 (size : positive) return natural is
    variable levels : natural := 0;
    variable span   : positive := 1;
  begin
    while span < size loop
      span   := 2 * span;
      levels := levels + 1;
    end loop;
    return levels;
  end function;

  constant LEVELS : natural := clog2(N);

  type state_t is (S_IDLE, S_GROW);
  signal state : state_t := S_IDLE;

  signal defect_r : std_logic_vector(N-1 downto 0) := (others => '0');
  signal grown_r  : std_logic_vector(N downto 0)   := (others => '0');
  signal half_r   : std_logic_vector(N downto 0)   := (others => '0');
  signal rounds_r : unsigned(7 downto 0)           := (others => '0');

  signal corr_r : std_logic_vector(N downto 0) := (others => '0');
  signal obs_r  : std_logic := '0';
  signal done_r : std_logic := '0';

  -- combinational results
  signal any_odd_c : std_logic;
  signal grown_n_c : std_logic_vector(N downto 0);
  signal half_n_c  : std_logic_vector(N downto 0);
  signal corr_c    : std_logic_vector(N downto 0);

begin

  ------------------------------------------------------------------------------
  -- Combinational cluster info, growth step and peeling
  ------------------------------------------------------------------------------
  comb : process (all)
    variable L, R, BL, BR, odd : std_logic_vector(N-1 downto 0);
    variable stride            : positive;
    variable inc               : natural range 0 to 2;
    variable g                 : natural range 0 to 3;
    variable anyo              : std_logic;
  begin
    -- L(v): XOR of the defects from the start of v's segment to v.
    -- BL(v): v's segment reaches the left boundary.
    -- R(v), BR(v): the same from v to the end of its segment, right boundary.
    if PARALLEL then
      -- Both are linear recurrences: L(v) = defect(v) xor (grown(v) and
      -- L(v-1)), and BL(v) = grown(0) and ... and grown(v). Steps compose with
      -- an associative operator, (p1, x1) then (p2, x2) = (p1 and p2,
      -- x2 xor (p2 and x1)), so a Kogge-Stone scan computes every L and BL in
      -- LEVELS levels. At level k, element v absorbs element v - 2**k; going
      -- down from v = N-1 reads each v - 2**k before this level overwrites it.
      for v in 0 to N-1 loop
        BL(v) := grown_r(v);    -- grown_r(0) is the left boundary edge
        L(v)  := defect_r(v);
        BR(v) := grown_r(v+1);  -- grown_r(N) is the right boundary edge
        R(v)  := defect_r(v);
      end loop;
      stride := 1;
      for level in 1 to LEVELS loop
        for v in N-1 downto 0 loop
          if v >= stride then
            L(v)  := L(v) xor (BL(v) and L(v - stride));
            BL(v) := BL(v) and BL(v - stride);
          end if;
        end loop;
        for v in 0 to N-1 loop  -- the mirror image, for the suffix
          if v + stride <= N-1 then
            R(v)  := R(v) xor (BR(v) and R(v + stride));
            BR(v) := BR(v) and BR(v + stride);
          end if;
        end loop;
        stride := 2 * stride;
      end loop;
    else
      -- prefix within segment (left to right), one detector after the other
      for v in 0 to N-1 loop
        if v = 0 then
          L(v)  := defect_r(v);
          BL(v) := grown_r(0);
        elsif grown_r(v) = '1' then
          L(v)  := defect_r(v) xor L(v-1);
          BL(v) := BL(v-1);
        else
          L(v)  := defect_r(v);
          BL(v) := '0';
        end if;
      end loop;

      -- suffix within segment (right to left)
      for v in N-1 downto 0 loop
        if v = N-1 then
          R(v)  := defect_r(v);
          BR(v) := grown_r(N);
        elsif grown_r(v+1) = '1' then
          R(v)  := defect_r(v) xor R(v+1);
          BR(v) := BR(v+1);
        else
          R(v)  := defect_r(v);
          BR(v) := '0';
        end if;
      end loop;
    end if;

    -- a cluster is odd if its parity is 1 and it touches no boundary
    anyo := '0';
    for v in 0 to N-1 loop
      odd(v) := (L(v) xor R(v) xor defect_r(v)) and not (BL(v) or BR(v));
      anyo   := anyo or odd(v);
    end loop;
    any_odd_c <= anyo;

    -- growth: each odd neighbour adds one half-edge
    for k in 0 to N loop
      -- nested ifs so no out-of-range index is ever formed at k = 0 or k = N
      inc := 0;
      if k >= 1 then
        if odd(k-1) = '1' then
          inc := inc + 1;
        end if;
      end if;
      if k <= N-1 then
        if odd(k) = '1' then
          inc := inc + 1;
        end if;
      end if;
      if grown_r(k) = '1' then
        grown_n_c(k) <= '1';
        half_n_c(k)  <= '0';
      else
        if half_r(k) = '1' then
          g := 1 + inc;
        else
          g := inc;
        end if;
        if g >= 2 then grown_n_c(k) <= '1'; else grown_n_c(k) <= '0'; end if;
        if g = 1  then half_n_c(k)  <= '1'; else half_n_c(k)  <= '0'; end if;
      end if;
    end loop;

    -- peeling: proven valid in hardware/README.md
    for k in 0 to N loop
      if grown_r(k) = '0' then
        corr_c(k) <= '0';
      elsif k = 0 then
        corr_c(k) <= R(0);
      elsif k = N then
        if BL(N-1) = '1' then
          corr_c(k) <= '0';
        else
          corr_c(k) <= L(N-1);
        end if;
      else
        if BL(k-1) = '1' then
          corr_c(k) <= R(k);
        else
          corr_c(k) <= L(k-1);
        end if;
      end if;
    end loop;
  end process comb;

  ------------------------------------------------------------------------------
  -- Sequential control
  ------------------------------------------------------------------------------
  seq : process (clk)
  begin
    if rising_edge(clk) then
      done_r <= '0';
      if rst = '1' then
        state    <= S_IDLE;
        defect_r <= (others => '0');
        grown_r  <= (others => '0');
        half_r   <= (others => '0');
        rounds_r <= (others => '0');
        corr_r   <= (others => '0');
        obs_r    <= '0';
      else
        case state is
          when S_IDLE =>
            if start = '1' then
              defect_r <= syndrome;
              grown_r  <= (others => '0');
              half_r   <= (others => '0');
              rounds_r <= (others => '0');
              state    <= S_GROW;
            end if;

          when S_GROW =>
            if any_odd_c = '0' then
              -- every cluster is even: peel and publish
              corr_r <= corr_c;
              obs_r  <= corr_c(0);
              done_r <= '1';
              state  <= S_IDLE;
            else
              grown_r  <= grown_n_c;
              half_r   <= half_n_c;
              rounds_r <= rounds_r + 1;
            end if;
        end case;
      end if;
    end if;
  end process seq;

  busy       <= '1' when state = S_GROW else '0';
  done       <= done_r;
  correction <= corr_r;
  obs_flip   <= obs_r;
  rounds     <= rounds_r;

end architecture rtl;
